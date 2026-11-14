"""Talking to Nostr relays: publish, query, follow (SPEC.md §5, §9).

Relays are unreliable in the ways docs/transport-test.md records: some are
down, some drop writes without a reply, some rate-limit. Every operation here
works across several relays, treats each one's failure as normal, and records
it in RelayHealth so persistently failing relays are skipped for a while.
"""

import asyncio
import json
import secrets
import ssl
import time
from collections import OrderedDict
from pathlib import Path

import certifi
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .event import KIND, NAMESPACE
from .home import home, write_atomic

DEFAULT_RELAYS = [
    "wss://relay.primal.net",
    "wss://relay.nostr.net",
    "wss://relay.snort.social",
    "wss://nostr.oxtr.dev",
    "wss://offchain.pub",
]
TIMEOUT = 10.0
MAX_MESSAGE_BYTES = 512 * 1024
FAILURES_BEFORE_BACKOFF = 3
BACKOFF_START = 600
BACKOFF_MAX = 6 * 3600

# certifi because python.org builds on macOS ship without a CA bundle.
_TLS = ssl.create_default_context(cafile=certifi.where())


def topic_filter(topic, limit=200):
    return {"kinds": [KIND], "#L": [NAMESPACE], "#t": [topic], "limit": limit}


class RelayHealth:
    """Per-relay success and failure counts, with a backoff after repeated
    failures. Saved to disk so a restart does not retry dead relays at once."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else home() / "relays.json"
        try:
            self.state = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            self.state = {}

    def _get(self, url):
        return self.state.setdefault(url, {"ok": 0, "fail": 0, "consecutive": 0,
                                           "retry_after": 0, "last_error": ""})

    def usable(self, url, now):
        return now >= self._get(url)["retry_after"]

    def success(self, url):
        s = self._get(url)
        s["ok"] += 1
        s["consecutive"] = 0
        s["retry_after"] = 0

    def failure(self, url, error, now):
        s = self._get(url)
        s["fail"] += 1
        s["consecutive"] += 1
        s["last_error"] = str(error)[:200]
        extra = s["consecutive"] - FAILURES_BEFORE_BACKOFF
        if extra >= 0:
            s["retry_after"] = now + min(BACKOFF_START * 2 ** extra, BACKOFF_MAX)

    def choose(self, urls, now, minimum):
        """Usable relays first; if fewer than `minimum`, add backed-off ones
        anyway rather than not talking to the network at all."""
        usable = [u for u in urls if self.usable(u, now)]
        rest = sorted((u for u in urls if u not in usable),
                      key=lambda u: self._get(u)["retry_after"])
        return usable + rest[:max(0, minimum - len(usable))]

    def save(self):
        write_atomic(self.path, json.dumps(self.state, indent=2).encode())


def _parse(raw):
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    return msg if isinstance(msg, list) and msg and isinstance(msg[0], str) else None


async def _recv(ws, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("relay did not answer in time")
    return _parse(await asyncio.wait_for(ws.recv(), remaining))


async def _req(ws, flt, deadline):
    """One REQ; return the events sent before EOSE."""
    sub = secrets.token_hex(4)
    await ws.send(json.dumps(["REQ", sub, flt]))
    events = []
    while True:
        msg = await _recv(ws, deadline)
        if not msg or len(msg) < 2 or msg[1] != sub:
            continue
        if msg[0] == "EVENT" and len(msg) >= 3 and isinstance(msg[2], dict):
            events.append(msg[2])
        elif msg[0] == "EOSE":
            await ws.send(json.dumps(["CLOSE", sub]))
            return events
        elif msg[0] == "CLOSED":
            raise ConnectionError(f"subscription closed: {msg[2:3]}")


class RelayPool:
    def __init__(self, relays=None, health: RelayHealth | None = None,
                 timeout=TIMEOUT, minimum=3):
        self.relays = list(relays or DEFAULT_RELAYS)
        self.health = health if health is not None else RelayHealth()
        self.timeout = timeout
        self.minimum = minimum

    def _connect(self, url):
        return connect(url, open_timeout=self.timeout, close_timeout=2,
                       max_size=MAX_MESSAGE_BYTES,
                       ssl=_TLS if url.startswith("wss://") else None)

    def _chosen(self):
        return self.health.choose(self.relays, time.time(), self.minimum)

    async def _each(self, fn):
        """Run fn(url) on every chosen relay at once. Returns {url: result or
        exception}, and records health."""
        urls = self._chosen()
        results = await asyncio.gather(*(fn(u) for u in urls), return_exceptions=True)
        out = {}
        for url, r in zip(urls, results):
            if isinstance(r, (OSError, TimeoutError, WebSocketException, ConnectionError)):
                self.health.failure(url, f"{type(r).__name__}: {r}", time.time())
            elif isinstance(r, BaseException):
                raise r
            else:
                self.health.success(url)
            out[url] = r
        self.health.save()
        return out

    async def publish(self, ev):
        """Send `ev` to every chosen relay and confirm by reading it back.

        Returns {url: status}, status one of: confirmed, accepted (OK but not
        readable back), rejected: <reason>, no-reply, or error: <reason>.
        Relays that drop writes silently show up as no-reply.
        """
        async def one(url):
            deadline = time.monotonic() + self.timeout
            async with self._connect(url) as ws:
                await ws.send(json.dumps(["EVENT", ev]))
                while True:
                    try:
                        msg = await _recv(ws, deadline)
                    except TimeoutError:
                        return "no-reply"
                    if msg and msg[0] == "OK" and len(msg) >= 3 and msg[1] == ev["id"]:
                        break
                if not msg[2]:
                    return f"rejected: {msg[3] if len(msg) > 3 else ''}"
                found = await _req(ws, {"ids": [ev["id"]]}, time.monotonic() + self.timeout)
                return "confirmed" if any(e.get("id") == ev["id"] for e in found) else "accepted"

        results = await self._each(one)
        return {u: r if isinstance(r, str) else f"error: {type(r).__name__}: {r}"
                for u, r in results.items()}

    async def publish_many(self, events, interval=1.0):
        """Publish events one after another, `interval` seconds apart, since
        relays rate-limit bursts (docs/transport-test.md)."""
        results = []
        for i, ev in enumerate(events):
            if i:
                await asyncio.sleep(interval)
            results.append(await self.publish(ev))
        return results

    async def query(self, flt):
        """Ask every chosen relay; merge, dropping duplicate ids.

        Returns (events, {url: count or error string}).
        """
        async def one(url):
            async with self._connect(url) as ws:
                return await _req(ws, flt, time.monotonic() + self.timeout)

        results = await self._each(one)
        seen, events, status = set(), [], {}
        for url, r in results.items():
            if isinstance(r, BaseException):
                status[url] = f"error: {type(r).__name__}: {r}"
                continue
            status[url] = len(r)
            for ev in r:
                eid = ev.get("id")
                if isinstance(eid, str) and eid not in seen:
                    seen.add(eid)
                    events.append(ev)
        return events, status

    async def follow(self, flt, max_backoff=60.0):
        """Yield (url, event) for matching events from every relay, live,
        reconnecting each relay on failure. Each event id is yielded once."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        async def one(url):
            backoff = 1.0
            since = None
            while True:
                try:
                    async with self._connect(url) as ws:
                        sub = secrets.token_hex(4)
                        f = dict(flt, since=since) if since else flt
                        await ws.send(json.dumps(["REQ", sub, f]))
                        self.health.success(url)
                        backoff = 1.0
                        async for raw in ws:
                            msg = _parse(raw)
                            if (msg and msg[0] == "EVENT" and len(msg) >= 3
                                    and msg[1] == sub and isinstance(msg[2], dict)):
                                created = msg[2].get("created_at")
                                if isinstance(created, int):
                                    # A minute of overlap on reconnect; ids dedupe it.
                                    since = max(since or 0, created - 60)
                                await queue.put((url, msg[2]))
                except (OSError, TimeoutError, WebSocketException) as e:
                    self.health.failure(url, f"{type(e).__name__}: {e}", time.time())
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

        tasks = [asyncio.create_task(one(u)) for u in self._chosen()]
        seen = OrderedDict()
        try:
            while True:
                url, ev = await queue.get()
                eid = ev.get("id")
                if not isinstance(eid, str) or eid in seen:
                    continue
                seen[eid] = None
                if len(seen) > 10000:
                    seen.popitem(last=False)
                yield url, ev
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.health.save()
