"""The receiver pipeline of SPEC.md §7."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from .check import run_check
from .claim import Claim, Rejected, parse_claim
from .event import KIND, MAX_CLOCK_SKEW, check_labels, check_tags, verify_signature, well_formed

SKEW = timedelta(seconds=MAX_CLOCK_SKEW)


@dataclass(frozen=True)
class Result:
    result: str                  # pass, fail, error, retracted, rejected
    reason: str = ""             # SPEC.md §7 code when rejected
    detail: str = ""
    claim: Claim | None = None
    checked_url: str = ""


def _times(claim, now, created_at=None):
    """Steps 13-15."""
    if created_at is not None:
        gap = abs(created_at - claim.generated_at.timestamp())
        if gap > MAX_CLOCK_SKEW:
            raise Rejected("bad-time", "created_at too far from generated.at")
    if claim.generated_at > now + SKEW:
        raise Rejected("bad-time", "generated.at is in the future")
    if now >= claim.stale_after:
        raise Rejected("expired", f"stale since {claim.stale_after.isoformat()}")


def _finish(claim, fetcher):
    """Steps 16-17."""
    if claim.retracted:
        return Result("retracted", claim=claim)
    r = run_check(claim.topic, claim.check, fetcher)
    return Result(r.result, detail=r.detail, claim=claim, checked_url=r.final_url)


def static_event(ev, now: datetime) -> Claim:
    """Steps 1-15 of SPEC.md §7: everything that needs no network.
    Raises Rejected."""
    if not isinstance(ev, dict):
        raise Rejected("wrong-kind", "event is not an object")
    if ev.get("kind") != KIND:
        raise Rejected("wrong-kind", str(ev.get("kind")))
    if not well_formed(ev):
        raise Rejected("bad-event", "fields do not have NIP-01 types")
    if not verify_signature(ev):
        raise Rejected("bad-signature")
    check_labels(ev)
    claim = parse_claim(ev["content"])
    check_tags(ev, claim)
    _times(claim, now, ev.get("created_at"))
    return claim


def validate_event(ev, now: datetime, fetcher):
    """Run every step of SPEC.md §7 on a Nostr event."""
    try:
        claim = static_event(ev, now)
    except Rejected as e:
        return Result("rejected", e.reason, e.detail)
    return _finish(claim, fetcher)


def validate_file(content, now: datetime, fetcher):
    """Validate a bare claim file: the steps of §7 that do not need an event."""
    try:
        claim = parse_claim(content)
        _times(claim, now)
    except Rejected as e:
        return Result("rejected", e.reason, e.detail)
    return _finish(claim, fetcher)
