import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from afx.check import Response
from afx.claim import parse_claim
from afx.event import Signer
from afx.receiver import Receiver, newest_per_publisher
from afx.relay import RelayHealth, RelayPool, topic_filter
from afx.reputation import Reputation
from afx.store import Store

from fake_relay import FakeRelay

GEN = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
URL = "https://registry.npmjs.org/-/package/left-pad/dist-tags"


def claim(expect="1.3.0", status="stable", gen=GEN):
    z = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return parse_claim(
        f"---\ntype: Claim\ntopic: npm:left-pad\nstatus: {status}\n"
        f"generated: {{ by: test/0, at: {z(gen)} }}\n"
        f"stale_after: {z(gen + timedelta(days=1))}\n"
        f"check:\n  kind: json-field-equals\n  url: {URL}\n"
        f"  pointer: /latest\n  expect: \"{expect}\"\n---\n")


def signed(signer, c):
    return signer.claim_event(c, int(c.generated_at.timestamp()))


class CountingFetcher:
    def __init__(self, body='{"latest":"1.3.0"}'):
        self.body = body
        self.gets = 0

    def resolve(self, host):
        return ["104.16.0.35"]

    def get(self, url, address):
        self.gets += 1
        return Response(200, {}, self.body.encode())


@pytest.fixture
def health(tmp_path):
    return RelayHealth(tmp_path / "relays.json")


async def _dead_url():
    r = await FakeRelay().start()
    await r.stop()
    return r.url


def test_publish_reports_each_relay(health):
    async def go():
        ok, silent, reject = [await FakeRelay(m).start() for m in ("ok", "silent", "reject")]
        dead = await _dead_url()
        pool = RelayPool([ok.url, silent.url, reject.url, dead], health, timeout=1)
        ev = signed(Signer(), claim())
        status = await pool.publish(ev)
        for r in (ok, silent, reject):
            await r.stop()
        return ok, silent, reject, dead, status

    ok, silent, reject, dead, status = asyncio.run(go())
    assert status[ok.url] == "confirmed"
    assert status[silent.url] == "no-reply"
    assert status[reject.url] == "rejected: blocked: test"
    assert status[dead].startswith("error:")
    assert health.state[dead]["consecutive"] == 1
    assert health.state[ok.url]["ok"] == 1


def test_query_merges_and_dedupes(health):
    async def go():
        a, b, c = [await FakeRelay().start() for _ in range(3)]
        dead = await _dead_url()
        e1 = signed(Signer(), claim())
        e2 = signed(Signer(), claim("1.4.0"))
        for r in (a, b):
            r.add(e1)
        c.add(e2)
        events, status = await RelayPool([a.url, b.url, c.url, dead], health,
                                         timeout=1).query(topic_filter("npm:left-pad"))
        for r in (a, b, c):
            await r.stop()
        return {e["id"] for e in events}, {e1["id"], e2["id"]}, status, dead

    got, want, status, dead = asyncio.run(go())
    assert got == want
    assert status[dead].startswith("error:")


def test_query_filters_by_label_and_topic(health):
    async def go():
        r = await FakeRelay().start()
        mine = signed(Signer(), claim())
        other = dict(mine, tags=[t for t in mine["tags"] if t[0] != "L"], id="x" * 64)
        r.add(mine)
        r.add(other)
        events, _ = await RelayPool([r.url], health, timeout=1).query(
            topic_filter("npm:left-pad"))
        none, _ = await RelayPool([r.url], health, timeout=1).query(topic_filter("npm:react"))
        await r.stop()
        return events, none, mine

    events, none, mine = asyncio.run(go())
    assert [e["id"] for e in events] == [mine["id"]]
    assert none == []


def test_health_backs_off_and_falls_back(health):
    for _ in range(3):
        health.failure("wss://dead", "refused", now=1000)
    assert not health.usable("wss://dead", 1000)
    assert health.usable("wss://dead", 1000 + 600)
    urls = ["wss://a", "wss://b", "wss://c", "wss://dead"]
    assert health.choose(urls, 1000, minimum=3) == ["wss://a", "wss://b", "wss://c"]
    assert health.choose(["wss://a", "wss://dead"], 1000, minimum=3) == ["wss://a", "wss://dead"]
    health.failure("wss://dead", "refused", now=1000)
    assert health.state["wss://dead"]["retry_after"] == 1000 + 1200
    health.success("wss://dead")
    assert health.usable("wss://dead", 1000)


def test_follow_reconnects_without_duplicates(health):
    async def go():
        relay = await FakeRelay().start()
        pool = RelayPool([relay.url], health, timeout=1)
        signer = Signer()
        first = signed(signer, claim("1.3.0"))
        second = signed(Signer(), claim("1.3.1"))
        relay.add(first)
        got = []

        async def consume():
            async for url, ev in pool.follow(topic_filter("npm:left-pad"), max_backoff=0.2):
                got.append(ev["id"])
                if len(got) == 2:
                    return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        port = relay.port
        await relay.stop()                       # relay goes down
        relay2 = FakeRelay()
        relay2.port = port
        relay2.add(first)                        # same history after restart
        relay2.add(second)                       # plus a new event
        await relay2.start()
        await asyncio.wait_for(task, 5)
        await relay2.stop()
        return got, first, second

    got, first, second = asyncio.run(go())
    assert got == [first["id"], second["id"]]


def test_newest_per_publisher():
    signer = Signer()
    c_old, c_new = claim("1.3.0"), claim("1.4.0", gen=GEN + timedelta(minutes=1))
    old, new = signed(signer, c_old), signed(signer, c_new)
    c_other = claim("1.3.0")
    other = signed(Signer(), c_other)
    kept = {e["id"] for e, _ in newest_per_publisher(
        [(old, c_old), (new, c_new), (other, c_other)])}
    assert kept == {new["id"], other["id"]}


def test_forged_newer_event_cannot_displace_real_claim(tmp_path, health):
    """An event claiming alice's key, newer, with a bad signature, must not
    hide alice's real claim."""
    async def go():
        r = await FakeRelay().start()
        alice = Signer()
        real = signed(alice, claim())
        later = claim("1.3.0", "deprecated", GEN + timedelta(minutes=1))
        forged = dict(Signer().claim_event(later, int(later.generated_at.timestamp())),
                      pubkey=alice.pubkey)
        r.add(real)
        r.events[forged["id"]] = forged
        rec = _receiver(tmp_path, [r.url], health, CountingFetcher())
        found, stats = await rec.lookup("npm:left-pad")
        await r.stop()
        return found, stats, rec, alice

    found, stats, rec, alice = asyncio.run(go())
    assert [f["publishers"] for f in found] == [1]
    assert stats.rejected == {"bad-signature": 1} and stats.retracted == 0
    assert rec.reputation.counts[alice.pubkey]["rejected"] == 0


def _receiver(tmp_path, relay_urls, health, fetcher, **kw):
    return Receiver(RelayPool(relay_urls, health, timeout=1), Store(tmp_path / "store"),
                    Reputation(tmp_path / "rep.json"), fetcher, **kw)


def test_receiver_end_to_end(tmp_path, health):
    async def go():
        r1, r2 = await FakeRelay().start(), await FakeRelay().start()
        alice, bob, mallory, carol = Signer(), Signer(), Signer(), Signer()
        true_a, true_b = signed(alice, claim()), signed(bob, claim())
        false_m = signed(mallory, claim("9.9.9"))
        forged = dict(signed(mallory, claim()), sig="0" * 128)
        published = signed(carol, claim("1.3.0"))
        retraction = signed(carol, claim("1.3.0", "deprecated", GEN + timedelta(minutes=1)))
        for ev in (true_a, false_m, published):
            r1.add(ev)
        r1.events[forged["id"]] = forged     # bypass the relay's replace rule
        r2.add(true_b)
        r2.add(true_a)
        fetcher = CountingFetcher()
        rec = _receiver(tmp_path, [r1.url, r2.url], health, fetcher)

        first, stats1 = await rec.lookup("npm:left-pad")
        r1.add(retraction)
        second, stats2 = await rec.lookup("npm:left-pad")
        for r in (r1, r2):
            await r.stop()
        return first, stats1, second, stats2, fetcher, rec, (alice, carol, mallory)

    first, s1, second, s2, fetcher, rec, (alice, carol, mallory) = asyncio.run(go())

    [fact] = first
    assert fact["expect"] == "1.3.0" and fact["publishers"] == 3
    assert set(fact) == {"topic", "kind", "url", "pointer", "expect", "publishers",
                         "checked_at", "stale_after"}
    assert s1.rejected == {"bad-signature": 1}
    assert s1.results == {"pass": 1, "fail": 1}
    assert s1.checks_run == 2, "three publishers of one claim share one check"

    [fact] = second
    assert fact["publishers"] == 2, "carol's retraction removes her claim"
    assert s2.retracted == 1
    assert rec.reputation.counts[mallory.pubkey]["fail"] == 2
    assert rec.reputation.counts[alice.pubkey]["pass"] == 2


def test_receiver_check_budget_prefers_track_record(tmp_path, health):
    async def go():
        r = await FakeRelay().start()
        known, strangers = Signer(), [Signer() for _ in range(3)]
        good = signed(known, claim("1.3.0"))
        r.add(good)
        for i, s in enumerate(strangers):
            r.add(signed(s, claim(f"9.9.{i}")))
        rec = _receiver(tmp_path, [r.url], health, CountingFetcher(), max_checks=1)
        rec.reputation.counts[known.pubkey] = {"pass": 5, "fail": 0, "rejected": 0,
                                               "retracted": 0}
        found, stats = await rec.lookup("npm:left-pad")
        await r.stop()
        return found, stats

    found, stats = asyncio.run(go())
    assert [f["expect"] for f in found] == ["1.3.0"]
    assert stats.checks_run == 1 and stats.checks_skipped == 3


def test_receiver_rate_limits_a_flooding_key(tmp_path, health):
    async def go():
        r = await FakeRelay().start()
        flooder = Signer()
        for i in range(5):
            c = parse_claim(
                "---\ntype: Claim\ntopic: npm:left-pad\n"
                f"generated: {{ by: t/0, at: {GEN:%Y-%m-%dT%H:%M:%SZ} }}\n"
                f"stale_after: {GEN + timedelta(days=1):%Y-%m-%dT%H:%M:%SZ}\n"
                f"check:\n  kind: json-field-equals\n  url: {URL}\n"
                f"  pointer: /p{i}\n  expect: \"x\"\n---\n")
            r.add(signed(flooder, c))
        rec = _receiver(tmp_path, [r.url], health, CountingFetcher())
        rec.reputation.per_minute = 2
        _, stats = await rec.lookup("npm:left-pad")
        await r.stop()
        return stats

    stats = asyncio.run(go())
    assert stats.rate_limited == 3 and stats.checks_run == 2


def test_receiver_sets_aside_off_topic_events(tmp_path, health):
    """A relay that ignores #t sends other topics' events; they are not judged."""
    async def go():
        r = await FakeRelay().start()
        mine = signed(Signer(), claim())
        stranger = Signer()
        other = stranger.sign({"created_at": mine["created_at"], "kind": 30078,
                               "tags": [["d", "afx:x"], ["L", "afx"], ["l", "claim", "afx"],
                                        ["t", "npm:react"]],
                               "content": "not a claim at all"})
        r.add(mine)
        r.events[other["id"]] = other
        rec = _receiver(tmp_path, [r.url], health, CountingFetcher())
        rec.pool.query = lambda flt: _all(r)      # behave like a relay ignoring #t
        found, stats = await rec.lookup("npm:left-pad")
        await r.stop()
        return found, stats, rec, stranger

    async def _all(r):
        return list(r.events.values()), {r.url: len(r.events)}

    found, stats, rec, stranger = asyncio.run(go())
    assert len(found) == 1
    assert stats.off_topic == 1 and stats.rejected == {}
    assert stranger.pubkey not in rec.reputation.counts
