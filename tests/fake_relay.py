"""A minimal in-process Nostr relay for tests.

Supports EVENT, REQ (with live delivery after EOSE), and CLOSE; filters on
ids, authors, kinds, since, limit, and single-letter tags; keeps one event per
(pubkey, kind, d) for kinds 30000-39999. `mode` simulates the relay behaviours
recorded in docs/transport-test.md.
"""

import json

from websockets.asyncio.server import serve


def matches(ev, flt):
    if "ids" in flt and ev["id"] not in flt["ids"]:
        return False
    if "authors" in flt and ev["pubkey"] not in flt["authors"]:
        return False
    if "kinds" in flt and ev["kind"] not in flt["kinds"]:
        return False
    if "since" in flt and ev["created_at"] < flt["since"]:
        return False
    for key, values in flt.items():
        if key.startswith("#") and len(key) == 2:
            have = {t[1] for t in ev["tags"] if len(t) > 1 and t[0] == key[1]}
            if not have & set(values):
                return False
    return True


def _d(ev):
    return next((t[1] for t in ev["tags"] if t[0] == "d" and len(t) > 1), "")


class FakeRelay:
    def __init__(self, mode="ok"):
        self.mode = mode          # ok | silent | reject
        self.events = {}
        self.subs = {}            # (ws, sub id) -> filter
        self.received = 0
        self.server = None
        self.url = None
        self.port = 0

    async def start(self):
        self.server = await serve(self._handle, "127.0.0.1", self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}"
        return self

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    def add(self, ev):
        if 30000 <= ev["kind"] < 40000:
            for eid, old in list(self.events.items()):
                if (old["pubkey"], old["kind"], _d(old)) == (ev["pubkey"], ev["kind"], _d(ev)):
                    if old["created_at"] >= ev["created_at"]:
                        return False
                    del self.events[eid]
        self.events[ev["id"]] = ev
        return True

    async def _handle(self, ws):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg[0] == "EVENT":
                    await self._event(ws, msg[1])
                elif msg[0] == "REQ":
                    sub, flt = msg[1], msg[2]
                    found = sorted((e for e in self.events.values() if matches(e, flt)),
                                   key=lambda e: -e["created_at"])[:flt.get("limit", 500)]
                    for e in found:
                        await ws.send(json.dumps(["EVENT", sub, e]))
                    await ws.send(json.dumps(["EOSE", sub]))
                    self.subs[(ws, sub)] = flt
                elif msg[0] == "CLOSE":
                    self.subs.pop((ws, msg[1]), None)
        finally:
            for key in [k for k in self.subs if k[0] is ws]:
                del self.subs[key]

    async def _event(self, ws, ev):
        self.received += 1
        if self.mode == "silent":
            return
        if self.mode == "reject":
            await ws.send(json.dumps(["OK", ev["id"], False, "blocked: test"]))
            return
        stored = self.add(ev)
        await ws.send(json.dumps(["OK", ev["id"], True, "" if stored else "duplicate: older"]))
        if stored:
            for (other, sub), flt in list(self.subs.items()):
                if matches(ev, flt):
                    await other.send(json.dumps(["EVENT", sub, ev]))
