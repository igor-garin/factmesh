"""Nostr events carrying claims (SPEC.md §5, NIP-01)."""

import hashlib
import json
import re
import secrets
import time

from coincurve import PrivateKey, PublicKeyXOnly

from .claim import Claim, Rejected

KIND = 30078
NAMESPACE = "factmesh"
LABEL = "claim"
MAX_CLOCK_SKEW = 300


_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX128 = re.compile(r"[0-9a-f]{128}")


def well_formed(ev):
    """NIP-01 field types. Checked before anything does arithmetic or
    comparison on the fields, since a validly signed event can still carry,
    say, a string created_at."""
    def is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)
    tags = ev.get("tags")
    return (isinstance(ev.get("id"), str) and _HEX64.fullmatch(ev["id"]) is not None
            and isinstance(ev.get("pubkey"), str) and _HEX64.fullmatch(ev["pubkey"]) is not None
            and isinstance(ev.get("sig"), str) and _HEX128.fullmatch(ev["sig"]) is not None
            and is_int(ev.get("created_at")) and 0 <= ev["created_at"] < 2**40
            and is_int(ev.get("kind"))
            and isinstance(ev.get("content"), str)
            and isinstance(tags, list)
            and all(isinstance(t, list) and t and all(isinstance(x, str) for x in t)
                    for t in tags))


def event_id(ev):
    payload = [0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]]
    ser = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(ser.encode()).hexdigest()


def verify_signature(ev):
    try:
        if event_id(ev) != ev["id"]:
            return False
        key = PublicKeyXOnly(bytes.fromhex(ev["pubkey"]))
        return key.verify(bytes.fromhex(ev["sig"]), bytes.fromhex(ev["id"]))
    except (KeyError, TypeError, ValueError):
        return False


def _tags(ev, name):
    return [t for t in ev.get("tags", []) if isinstance(t, list) and t and t[0] == name]


def check_labels(ev):
    """Step 3: exactly one `L factmesh` and one `l claim factmesh` (SPEC.md §5, §10)."""
    big = [t for t in _tags(ev, "L") if t[1:2] == [NAMESPACE]]
    small = [t for t in _tags(ev, "l") if len(t) >= 3 and t[2] == NAMESPACE]
    if len(big) != 1 or len(small) != 1 or small[0][1] != LABEL:
        raise Rejected("not-a-claim", "missing or unknown factmesh label")


def check_tags(ev, claim):
    """Step 11: `d`, `t`, and `expiration` each appear once and match the claim."""
    expected = {
        "d": f"{NAMESPACE}:{claim.key}",
        "t": claim.topic,
        "expiration": str(int(claim.stale_after.timestamp())),
    }
    for name, value in expected.items():
        found = _tags(ev, name)
        if len(found) != 1 or found[0][1:2] != [value]:
            raise Rejected("tag-mismatch", f"{name} tag must be exactly {value!r}")


class Signer:
    """Signs claim events with one local key."""

    def __init__(self, secret: bytes | None = None):
        self._key = PrivateKey(secret or secrets.token_bytes(32))
        self.pubkey = self._key.public_key_xonly.format().hex()

    @property
    def secret(self):
        return self._key.secret

    def sign(self, ev):
        ev = {**ev, "pubkey": self.pubkey}
        ev["id"] = event_id(ev)
        ev["sig"] = self._key.sign_schnorr(bytes.fromhex(ev["id"])).hex()
        return ev

    def claim_event(self, claim: Claim, created_at: int | None = None):
        """Build and sign the event for `claim` (SPEC.md §5)."""
        tags = [
            ["d", f"{NAMESPACE}:{claim.key}"],
            ["L", NAMESPACE],
            ["l", LABEL, NAMESPACE],
            ["t", claim.topic],
            ["expiration", str(int(claim.stale_after.timestamp()))],
        ]
        created = created_at if created_at is not None else int(time.time())
        return self.sign({"created_at": created, "kind": KIND, "tags": tags,
                          "content": claim.content})
