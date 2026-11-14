"""Local store of verified claims: a folder of markdown files.

Layout: <root>/<topic prefix>/<topic name>/<claim key>-<publisher>.md holds the
claim file as published, and a .json file beside it holds the event and when
it was checked. The .json file is the record; the .md file is for people and
for other markdown tools. Topic names are safe as paths because claim.py
validates them (no `..`, no empty segments).
"""

import json
from datetime import datetime
from pathlib import Path

from .claim import Claim
from .home import home, write_atomic


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else home() / "claims"

    def _base(self, topic, key, pubkey):
        prefix, _, name = topic.partition(":")
        return self.root / prefix / name / f"{key}-{pubkey[:16]}"

    def _read(self, base):
        try:
            return json.loads(base.with_suffix(".json").read_text())
        except FileNotFoundError:
            return None

    def put(self, ev, claim: Claim, checked_at: datetime) -> bool:
        """Save a claim whose check passed. Keeps only the newest event per
        publisher and claim key (SPEC.md §6). Returns False if `ev` is older."""
        base = self._base(claim.topic, claim.key, ev["pubkey"])
        old = self._read(base)
        if old and old["event"]["created_at"] >= ev["created_at"]:
            return False
        record = {
            "topic": claim.topic, "key": claim.key, "check": claim.check,
            "stale_after": int(claim.stale_after.timestamp()),
            "checked_at": int(checked_at.timestamp()), "event": ev,
        }
        # .md first: a crash between the writes leaves a file with no record,
        # which entries() ignores, rather than a record pointing at nothing.
        write_atomic(base.with_suffix(".md"), claim.content.encode())
        write_atomic(base.with_suffix(".json"), json.dumps(record, indent=2).encode())
        return True

    def retract(self, ev, claim: Claim) -> bool:
        """Apply a retraction from the same publisher, if it is newer."""
        base = self._base(claim.topic, claim.key, ev["pubkey"])
        old = self._read(base)
        if not old or old["event"]["created_at"] >= ev["created_at"]:
            return False
        self._delete(base)
        return True

    def entries(self, topic: str | None = None):
        start = self.root
        if topic:
            prefix, _, name = topic.partition(":")
            start = self.root / prefix / name
        if not start.exists():
            return []
        records = [json.loads(p.read_text()) for p in sorted(start.rglob("*.json"))]
        return [r for r in records if topic is None or r["topic"] == topic]

    def prune(self, now: datetime) -> int:
        """Delete claims at or past stale_after. Returns how many."""
        removed = 0
        if not self.root.exists():
            return 0
        for p in self.root.rglob("*.json"):
            if json.loads(p.read_text())["stale_after"] <= now.timestamp():
                self._delete(p.with_suffix(""))
                removed += 1
        return removed

    @staticmethod
    def _delete(base: Path):
        base.with_suffix(".json").unlink(missing_ok=True)
        base.with_suffix(".md").unlink(missing_ok=True)
