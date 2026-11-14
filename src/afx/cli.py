"""Command line: check a claim, sign a claim, show or rotate the key."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import keys
from .claim import Rejected, parse_claim
from .fetch import Fetcher
from .validate import validate_event, validate_file

EXIT_CODES = {"pass": 0, "fail": 1, "rejected": 2, "error": 3, "retracted": 4}


def _now(value):
    if value is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def cmd_check(args):
    text = Path(args.path).read_text()
    now = _now(args.now)
    if args.path.endswith(".json"):
        doc = json.loads(text)
        result = validate_event(doc.get("event", doc), now, Fetcher())
    else:
        result = validate_file(text, now, Fetcher())
    line = result.result
    if result.reason:
        line += f": {result.reason}"
    if result.detail:
        line += f" ({result.detail})"
    print(line)
    return EXIT_CODES[result.result]


def cmd_sign(args):
    try:
        claim = parse_claim(Path(args.path).read_text())
    except Rejected as e:
        print(f"not signed: {e}", file=sys.stderr)
        return EXIT_CODES["rejected"]
    ev = keys.load_signer().claim_event(claim, int(claim.generated_at.timestamp()))
    print(json.dumps(ev, indent=2, ensure_ascii=False))
    return 0


def cmd_key(args):
    signer = keys.rotate() if args.rotate else keys.load_signer()
    print(signer.pubkey)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="afx", description=__doc__)
    sub = ap.add_subparsers(required=True)

    p = sub.add_parser("check", help="validate a claim file (.md) or event (.json) "
                                     "and run its check against the web")
    p.add_argument("path")
    p.add_argument("--now", help="override the current time (ISO 8601, UTC)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("sign", help="sign a claim file with this installation's key")
    p.add_argument("path")
    p.set_defaults(func=cmd_sign)

    p = sub.add_parser("key", help="print this installation's public key")
    p.add_argument("--rotate", action="store_true", help="replace it with a new key")
    p.set_defaults(func=cmd_key)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
