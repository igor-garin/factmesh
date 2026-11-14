# /// script
# requires-python = ">=3.12"
# dependencies = ["certifi==2025.4.26", "coincurve==21.0.0", "websockets==15.0.1"]
# ///
"""Phase 1 transport test: will public Nostr relays carry claim events?

For each relay: read its NIP-11 info, publish signed test claims, then read
them back on a fresh connection. Measures acceptance per event kind, size
limits, tag filtering, burst rate limiting, and NIP-40 expiry.

Every event carries a NIP-40 expiration tag, so relays that honor it drop the
test data within a day. The signing key is generated per run and names no one.

Usage: uv run spikes/relay_test.py [--out results.json] [--expiry-wait 40]
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import ssl
import time
import urllib.request

import certifi
import websockets
from coincurve import PrivateKey

RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://relay.nostr.band",
    "wss://nostr.mom",
    "wss://relay.snort.social",
    "wss://offchain.pub",
    "wss://nostr.wine",
]

KIND_REGULAR = 4242      # unassigned in the NIPs kind list at test time
KIND_APPDATA = 30078     # NIP-78 application-specific data (addressable)
SIZES = [1_000, 16_000, 64_000, 128_000, 256_000]
BURST = 20
TIMEOUT = 10
# certifi because python.org builds on macOS ship without a CA bundle.
TLS = ssl.create_default_context(cafile=certifi.where())

SAMPLE_CLAIM = """---
type: Claim
title: npm left-pad latest is 1.3.0
resource: https://registry.npmjs.org/left-pad
tags: [npm, left-pad]
status: stable
generated: { by: relay-test/0, at: %(at)s }
stale_after: %(stale)s
check:
  kind: json-field-equals
  url: https://registry.npmjs.org/left-pad
  path: dist-tags.latest
  expect: "1.3.0"
---
Transport test event. Safe to discard.
"""


class Signer:
    def __init__(self):
        self.key = PrivateKey(secrets.token_bytes(32))
        self.pubkey = self.key.public_key_xonly.format().hex()

    def event(self, kind, content, tags):
        created = int(time.time())
        payload = [0, self.pubkey, created, kind, tags, content]
        serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        eid = hashlib.sha256(serialized.encode()).hexdigest()
        sig = self.key.sign_schnorr(bytes.fromhex(eid)).hex()
        return {"id": eid, "pubkey": self.pubkey, "created_at": created,
                "kind": kind, "tags": tags, "content": content, "sig": sig}


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def nip11(relay):
    url = relay.replace("wss://", "https://")
    req = urllib.request.Request(url, headers={"Accept": "application/nostr+json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=TLS) as r:
            info = json.loads(r.read(200_000))
        return {"supported_nips": info.get("supported_nips"),
                "limitation": info.get("limitation"),
                "software": info.get("software"), "version": info.get("version")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:200]}


async def publish(ws, event):
    """Send one event; return (accepted, message, seconds)."""
    start = time.monotonic()
    await ws.send(json.dumps(["EVENT", event]))
    while True:
        raw = await asyncio.wait_for(ws.recv(), TIMEOUT)
        msg = json.loads(raw)
        if msg[0] == "OK" and msg[1] == event["id"]:
            return bool(msg[2]), msg[3], round(time.monotonic() - start, 3)
        if msg[0] == "NOTICE":
            return False, f"NOTICE: {msg[1]}", round(time.monotonic() - start, 3)


async def query(ws, flt):
    """Run one REQ; return the list of events until EOSE."""
    sub = secrets.token_hex(4)
    await ws.send(json.dumps(["REQ", sub, flt]))
    events = []
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
        if msg[0] == "EVENT" and msg[1] == sub:
            events.append(msg[2])
        elif msg[0] == "EOSE" and msg[1] == sub:
            await ws.send(json.dumps(["CLOSE", sub]))
            return events
        elif msg[0] == "CLOSED" and msg[1] == sub:
            raise RuntimeError(f"CLOSED: {msg[2]}")


async def burst(ws, signer, tags):
    """Send BURST events back to back, then collect their OK replies.

    A websocket allows only one reader at a time, so replies are gathered in
    one loop rather than one publish() per event.
    """
    events = [signer.event(KIND_REGULAR, f"burst {i}", tags) for i in range(BURST)]
    pending = {e["id"] for e in events}
    for e in events:
        await ws.send(json.dumps(["EVENT", e]))
    accepted, reasons = 0, set()
    try:
        while pending:
            msg = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
            if msg[0] == "OK" and msg[1] in pending:
                pending.discard(msg[1])
                if msg[2]:
                    accepted += 1
                else:
                    reasons.add(msg[3])
            elif msg[0] == "NOTICE":
                reasons.add(f"NOTICE: {msg[1]}")
    except asyncio.TimeoutError:
        reasons.add(f"no reply for {len(pending)} events")
    return {"burst_accepted": accepted, "burst_rejections": sorted(reasons)[:3]}


def connect(relay):
    return websockets.connect(relay, open_timeout=TIMEOUT, max_size=2**22, ssl=TLS)


async def safe(coro):
    try:
        return await coro
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:200]}


async def test_relay(relay, signer, topic, expiry_wait):
    r = {"relay": relay, "nip11": await asyncio.to_thread(nip11, relay)}
    now = int(time.time())
    day = str(now + 86400)
    base_tags = [["t", topic], ["t", "npm:left-pad"], ["expiration", day]]
    content = SAMPLE_CLAIM % {"at": iso(now), "stale": iso(now + 86400)}

    regular = signer.event(KIND_REGULAR, content, base_tags)
    appdata = signer.event(KIND_APPDATA, content, base_tags + [["d", f"{topic}:left-pad"]])
    short = signer.event(KIND_REGULAR, content,
                         [["t", topic], ["expiration", str(now + 20)]])

    try:
        async with connect(relay) as ws:
            r["publish_regular"] = await safe(publish(ws, regular))
            r["publish_appdata"] = await safe(publish(ws, appdata))
            r["publish_short_expiry"] = await safe(publish(ws, short))

            sizes = {}
            for n in SIZES:
                ev = signer.event(KIND_REGULAR, "x" * n, base_tags)
                res = await safe(publish(ws, ev))
                sizes[n] = res
                if isinstance(res, dict) or not res[0]:
                    break
            r["sizes"] = sizes

            r.update(await safe(burst(ws, signer, base_tags)))
    except Exception as e:
        r["connect_error"] = f"{type(e).__name__}: {e}"[:200]
        return r

    async def readback():
        async with connect(relay) as ws:
            by_tag = await query(ws, {"kinds": [KIND_REGULAR, KIND_APPDATA],
                                      "#t": [topic], "authors": [signer.pubkey],
                                      "limit": 100})
            ids = {e["id"] for e in by_tag}
            other_tag = await query(ws, {"#t": ["npm:left-pad"],
                                         "authors": [signer.pubkey], "limit": 5})
            return {"tag_filter_returned": len(by_tag),
                    "regular_found": regular["id"] in ids,
                    "appdata_found": appdata["id"] in ids,
                    "second_tag_filter_works": len(other_tag) > 0,
                    "short_expiry_present_before_expiry": short["id"] in ids}
    r["readback"] = await safe(readback())

    await asyncio.sleep(expiry_wait)

    async def after_expiry():
        async with connect(relay) as ws:
            got = await query(ws, {"ids": [short["id"]]})
            return {"short_expiry_present_after_expiry": len(got) > 0}
    r["expiry"] = await safe(after_expiry())
    return r


async def main():
    global TIMEOUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/transport-test-results.json")
    ap.add_argument("--expiry-wait", type=int, default=40)
    ap.add_argument("--relays", nargs="+", default=RELAYS)
    ap.add_argument("--timeout", type=int, default=TIMEOUT)
    args = ap.parse_args()
    TIMEOUT = args.timeout

    signer = Signer()
    topic = f"relaytest:{secrets.token_hex(6)}"
    print(f"run topic {topic}, pubkey {signer.pubkey[:16]}…")
    results = await asyncio.gather(*[
        test_relay(relay, signer, topic, args.expiry_wait) for relay in args.relays])

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"run_at": iso(time.time()), "topic": topic,
                   "kinds": [KIND_REGULAR, KIND_APPDATA], "results": results},
                  f, indent=2, default=str)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
