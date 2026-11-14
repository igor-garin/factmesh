"""Receiving claims for a topic: fetch, validate, check, store (SPEC.md §6-§8)."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .check import run_check
from .claim import Rejected
from .relay import RelayPool, topic_filter
from .reputation import Reputation
from .store import Store
from .event import well_formed
from .validate import static_event

MAX_CHECKS_PER_LOOKUP = 20
CHECK_CONCURRENCY = 4
FOLLOW_CHECK_CACHE_SECONDS = 600


@dataclass
class LookupStats:
    events: int = 0
    off_topic: int = 0
    rejected: dict = field(default_factory=dict)
    rate_limited: int = 0
    retracted: int = 0
    checks_run: int = 0
    checks_skipped: int = 0
    results: dict = field(default_factory=dict)
    relays: dict = field(default_factory=dict)


def _check_id(check):
    return json.dumps(check, sort_keys=True, separators=(",", ":"))


def newest_per_publisher(pairs):
    """Keep one (event, claim) per publisher and claim key: the newest
    (SPEC.md §6). Call only on events whose signatures were verified: an
    unverified event could carry someone else's key and a later timestamp,
    and would displace their real claim."""
    best = {}
    for ev, claim in pairs:
        k = (ev["pubkey"], claim.key)
        if k not in best or ev["created_at"] > best[k][0]["created_at"]:
            best[k] = (ev, claim)
    return list(best.values())


def facts(store: Store, topic: str, now: datetime):
    """What an agent may see (SPEC.md §8): checks that passed here, how many
    distinct publishers sent each, and when it was last checked. Nothing the
    publisher wrote beyond the check itself."""
    groups = {}
    for r in store.entries(topic):
        if r["stale_after"] <= now.timestamp():
            continue
        g = groups.setdefault(_check_id(r["check"]), {
            "topic": r["topic"], "check": r["check"], "publishers": set(),
            "checked_at": 0, "stale_after": r["stale_after"]})
        g["publishers"].add(r["event"]["pubkey"])
        g["checked_at"] = max(g["checked_at"], r["checked_at"])
        g["stale_after"] = max(g["stale_after"], r["stale_after"])
    out = []
    for g in groups.values():
        out.append({
            "topic": g["topic"], **{k: g["check"][k] for k in g["check"]},
            "publishers": len(g["publishers"]),
            "checked_at": _iso(g["checked_at"]), "stale_after": _iso(g["stale_after"]),
        })
    return sorted(out, key=lambda f: (-f["publishers"], f["url"]))


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Receiver:
    def __init__(self, pool: RelayPool, store: Store, reputation: Reputation, fetcher,
                 max_checks=MAX_CHECKS_PER_LOOKUP):
        self.pool = pool
        self.store = store
        self.reputation = reputation
        self.fetcher = fetcher
        self.max_checks = max_checks
        self._recent_checks = {}       # check id -> (CheckResult, monotonic time)

    async def lookup(self, topic, now: datetime | None = None):
        """Fetch claims for `topic` from relays, verify them, and return
        (facts, stats). Facts include earlier verified claims still in the
        store."""
        now = now or datetime.now(timezone.utc)
        stats = LookupStats()
        events, stats.relays = await self.pool.query(topic_filter(topic))
        stats.events = len(events)

        valid = []
        for ev in events:
            # Some relays ignore part of the filter (docs/transport-test.md), so
            # events for other topics arrive too. Set them aside unjudged: the
            # relay chose to send them, not the publisher.
            tags = ev.get("tags") if isinstance(ev.get("tags"), list) else []
            if ["t", topic] not in [t[:2] for t in tags if isinstance(t, list)]:
                stats.off_topic += 1
                continue
            if not well_formed(ev):
                # Before rate limiting: that keys on pubkey, which could be
                # any JSON value here.
                stats.rejected["bad-event"] = stats.rejected.get("bad-event", 0) + 1
                continue
            pubkey = ev["pubkey"]
            if not self.reputation.allow(pubkey, time.time()):
                stats.rate_limited += 1
                continue
            try:
                claim = static_event(ev, now)
            except Rejected as e:
                stats.rejected[e.reason] = stats.rejected.get(e.reason, 0) + 1
                if e.reason != "bad-signature":
                    # A bad signature proves nothing about the key's owner.
                    self.reputation.record(pubkey, "rejected")
                continue
            if claim.topic != topic:
                stats.rejected["topic-mismatch"] = stats.rejected.get("topic-mismatch", 0) + 1
                continue
            valid.append((ev, claim))

        pending = {}       # check id -> [(event, claim)]
        for ev, claim in newest_per_publisher(valid):
            pubkey = ev["pubkey"]
            if claim.retracted:
                stats.retracted += 1
                self.store.retract(ev, claim)
                self.reputation.record(pubkey, "retracted")
                continue
            pending.setdefault(_check_id(claim.check), []).append((ev, claim))

        # One run per distinct check, however many publishers sent it. When
        # over budget, prefer checks backed by publishers with passing history,
        # then by more publishers.
        def priority(item):
            group = item[1]
            history = max(self.reputation.counts.get(ev["pubkey"], {}).get("pass", 0)
                          for ev, _ in group)
            return (-history, -len(group))

        ordered = sorted(pending.items(), key=priority)
        chosen, skipped = ordered[:self.max_checks], ordered[self.max_checks:]
        stats.checks_skipped = len(skipped)

        sem = asyncio.Semaphore(CHECK_CONCURRENCY)

        async def run(group):
            claim = group[0][1]
            async with sem:
                return await asyncio.to_thread(run_check, claim.topic, claim.check,
                                               self.fetcher)

        results = await asyncio.gather(*(run(g) for _, g in chosen))
        stats.checks_run = len(chosen)
        for (_, group), r in zip(chosen, results):
            stats.results[r.result] = stats.results.get(r.result, 0) + 1
            for ev, claim in group:
                self.reputation.record(ev["pubkey"], r.result)
                if r.result == "pass":
                    self.store.put(ev, claim, now)
        self.reputation.save()
        self.store.prune(now)
        return facts(self.store, topic, now), stats

    async def ingest(self, ev, topics, now: datetime | None = None):
        """Handle one live event from a subscription. Returns the result
        string, or None if the event was set aside or rate-limited.

        Results of identical checks are reused for FOLLOW_CHECK_CACHE_SECONDS,
        so a burst of copies of one claim costs one request.
        """
        now = now or datetime.now(timezone.utc)
        tags = ev.get("tags") if isinstance(ev.get("tags"), list) else []
        if not {t[1] for t in tags if isinstance(t, list) and len(t) > 1 and t[0] == "t"} & set(topics):
            return None
        if not well_formed(ev):
            return "rejected"
        pubkey = ev["pubkey"]
        if not self.reputation.allow(pubkey, time.time()):
            return None
        try:
            claim = static_event(ev, now)
        except Rejected as e:
            if e.reason != "bad-signature":
                self.reputation.record(pubkey, "rejected")
            return "rejected"
        if claim.retracted:
            self.store.retract(ev, claim)
            self.reputation.record(pubkey, "retracted")
            return "retracted"

        cid = _check_id(claim.check)
        cached = self._recent_checks.get(cid)
        if cached and time.monotonic() - cached[1] < FOLLOW_CHECK_CACHE_SECONDS:
            r = cached[0]
        else:
            r = await asyncio.to_thread(run_check, claim.topic, claim.check, self.fetcher)
            if r.result != "error":
                self._recent_checks[cid] = (r, time.monotonic())
        self.reputation.record(pubkey, r.result)
        if r.result == "pass":
            self.store.put(ev, claim, now)
        self.reputation.save()
        return r.result
