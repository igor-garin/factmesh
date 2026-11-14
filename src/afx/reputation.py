"""Per-publisher rate limiting and a local record of results (SPEC.md §7).

Kept only on this machine; reputation is never shared (DESIGN.md, out of
scope for v0).
"""

import json
from collections import defaultdict, deque
from pathlib import Path

from .home import home, write_atomic

COUNTED = ("pass", "fail", "rejected", "retracted")


class Reputation:
    def __init__(self, path: Path | None = None, per_minute=30):
        self.path = Path(path) if path else home() / "reputation.json"
        self.per_minute = per_minute
        self._recent = defaultdict(deque)
        try:
            self.counts = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.counts = {}

    def allow(self, pubkey, now: float) -> bool:
        """At most `per_minute` events per publisher in any 60 s window."""
        q = self._recent[pubkey]
        while q and q[0] <= now - 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True

    def record(self, pubkey, result):
        """Count a result. `error` is not counted: it says nothing about the
        publisher (SPEC.md §4.5)."""
        if result not in COUNTED:
            return
        c = self.counts.setdefault(pubkey, dict.fromkeys(COUNTED, 0))
        c[result] += 1

    def save(self):
        write_atomic(self.path, json.dumps(self.counts, indent=2).encode())
