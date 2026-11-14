"""Seed publisher: keeps the network stocked with "latest version" claims for
a list of popular packages, so the first user of an empty network finds facts
(PLAN.md Phase 7).

Each run reads the current latest version of every listed package, asks the
relays which claims this key already has, and publishes only claims that are
new, changed, or close to expiring. Run it on a schedule.
"""

import asyncio
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .check import observe_json, run_check
from .claim import Rejected, claim_key, claim_text, parse_claim, validate_topic
from .event import KIND, NAMESPACE, Signer
from .relay import RelayPool
from .validate import static_event

AGENT = f"factmesh-seed/{__version__}"


@dataclass(frozen=True)
class Target:
    topic: str
    url: str
    pointer: str

    @property
    def check_key(self):
        return claim_key({"kind": "json-field-equals", "url": self.url,
                          "pointer": self.pointer})


def load_targets(path) -> list[Target]:
    """Read the package list. Raises ValueError naming any bad entry."""
    data = tomllib.loads(Path(path).read_text())
    out = []
    for name in data.get("npm", []):
        out.append(Target(f"npm:{name}",
                          f"https://registry.npmjs.org/-/package/{name}/dist-tags", "/latest"))
    for name in data.get("pypi", []):
        out.append(Target(f"pypi:{name}", f"https://pypi.org/pypi/{name}/json",
                          "/info/version"))
    for t in out:
        try:
            validate_topic(t.topic)
        except Rejected:
            raise ValueError(f"not a valid package name: {t.topic}") from None
    return out


@dataclass
class SeedReport:
    published: list = field(default_factory=list)     # (topic, version, relay statuses)
    fresh: list = field(default_factory=list)         # topics whose claim is current
    skipped: list = field(default_factory=list)       # (topic, reason)
    would_publish: list = field(default_factory=list)  # dry run: (topic, version)


async def existing_claims(pool: RelayPool, pubkey, now):
    """This key's current claims on the relays: {claim key: Claim}."""
    events, _ = await pool.query({"kinds": [KIND], "#L": [NAMESPACE],
                                  "authors": [pubkey], "limit": 2000})
    held = {}
    for ev in events:
        if ev.get("pubkey") != pubkey:
            continue
        try:
            claim = static_event(ev, now)
        except Rejected:
            continue
        old = held.get(claim.key)
        if not old or claim.generated_at > old.generated_at:
            held[claim.key] = claim
    return held


async def run_seed(targets, pool: RelayPool, signer: Signer, fetcher, *,
                   valid_hours=24, refresh_hours=6, dry_run=False, interval=1.0,
                   now: datetime | None = None) -> SeedReport:
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    report = SeedReport()
    held = await existing_claims(pool, signer.pubkey, now)
    margin = timedelta(hours=refresh_hours)

    to_send = []
    for t in targets:
        value, why = await asyncio.to_thread(observe_json, t.topic, t.url, t.pointer, fetcher)
        if why:
            report.skipped.append((t.topic, why))
            continue
        old = held.get(t.check_key)
        if (old and not old.retracted and old.check["expect"] == value
                and old.stale_after - now > margin):
            report.fresh.append(t.topic)
            continue
        claim = parse_claim(claim_text(t.topic, "json-field-equals", t.url, value,
                                       t.pointer, valid_hours, AGENT, now))
        # Confirm with the same check a receiver will run, in case the
        # registry changed between reading and building the claim.
        r = await asyncio.to_thread(run_check, claim.topic, claim.check, fetcher)
        if r.result != "pass":
            report.skipped.append((t.topic, f"own check gave {r.result}: {r.detail}"))
            continue
        to_send.append((t.topic, value, claim))

    if dry_run:
        report.would_publish = [(topic, value) for topic, value, _ in to_send]
        return report
    events = [signer.claim_event(c, int(now.timestamp())) for _, _, c in to_send]
    statuses = await pool.publish_many(events, interval=interval)
    for (topic, value, _), status in zip(to_send, statuses):
        report.published.append((topic, value, status))
    return report
