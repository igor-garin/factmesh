"""Command line: check, sign, publish, and look up claims; run the MCP server;
show or rotate the key."""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import keys
from .check import run_check
from .claim import Rejected, parse_claim
from .fetch import Fetcher
from .receiver import Receiver
from .relay import RelayPool
from .reputation import Reputation
from .store import Store
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


def cmd_publish(args):
    try:
        claim = parse_claim(Path(args.path).read_text())
    except Rejected as e:
        print(f"not published: {e}", file=sys.stderr)
        return EXIT_CODES["rejected"]
    if claim.retracted:
        print("publishing a retraction; its check is not run", file=sys.stderr)
    else:
        r = run_check(claim.topic, claim.check, Fetcher())
        if r.result != "pass":
            print(f"not published: own check gave {r.result} ({r.detail})", file=sys.stderr)
            return EXIT_CODES[r.result]
    ev = keys.load_signer().claim_event(claim, int(claim.generated_at.timestamp()))
    status = asyncio.run(RelayPool(args.relay or None).publish(ev))
    for url, s in status.items():
        print(f"{url}  {s}")
    confirmed = sum(s == "confirmed" for s in status.values())
    print(f"{confirmed} of {len(status)} relays confirmed event {ev['id']}")
    return 0 if confirmed else EXIT_CODES["error"]


def cmd_lookup(args):
    receiver = Receiver(RelayPool(args.relay or None), Store(), Reputation(), Fetcher())
    found, stats = asyncio.run(receiver.lookup(args.topic))
    print(json.dumps({"facts": found, "stats": stats.__dict__}, indent=2))
    return 0


def cmd_seed(args):
    from .event import Signer
    from .seed import load_targets, run_seed
    try:
        targets = load_targets(args.packages)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if args.key_env:
        secret = os.environ.get(args.key_env, "").strip()
        if len(secret) != 64:
            print(f"{args.key_env} must hold a 64-character hex key", file=sys.stderr)
            return 2
        signer = Signer(bytes.fromhex(secret))
    else:
        signer = keys.load_signer()
    report = asyncio.run(run_seed(targets, RelayPool(args.relay or None), signer, Fetcher(),
                                  valid_hours=args.valid_hours,
                                  refresh_hours=args.refresh_hours, dry_run=args.dry_run))
    print(f"key {signer.pubkey}")
    for topic, value in report.would_publish:
        print(f"would publish  {topic} = {value}")
    unconfirmed = 0
    for topic, value, status in report.published:
        ok = sum(s == "confirmed" for s in status.values())
        unconfirmed += ok == 0
        print(f"published      {topic} = {value}  ({ok}/{len(status)} relays)")
    for topic, why in report.skipped:
        print(f"skipped        {topic}: {why}")
    print(f"{len(report.published)} published, {len(report.would_publish)} would publish, "
          f"{len(report.fresh)} already current, {len(report.skipped)} skipped")
    return 1 if unconfirmed else 0


def cmd_mcp(args):
    from .mcp_server import main as serve
    serve()
    return 0


def cmd_key(args):
    signer = keys.rotate() if args.rotate else keys.load_signer()
    print(signer.pubkey)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="factmesh", description=__doc__)
    sub = ap.add_subparsers(required=True)

    p = sub.add_parser("check", help="validate a claim file (.md) or event (.json) "
                                     "and run its check against the web")
    p.add_argument("path")
    p.add_argument("--now", help="override the current time (ISO 8601, UTC)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("sign", help="sign a claim file with this installation's key")
    p.add_argument("path")
    p.set_defaults(func=cmd_sign)

    p = sub.add_parser("publish", help="check a claim file yourself, sign it, and "
                                       "send it to relays")
    p.add_argument("path")
    p.add_argument("--relay", action="append", help="relay URL (repeatable)")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("lookup", help="fetch, verify, and print facts for a topic")
    p.add_argument("topic")
    p.add_argument("--relay", action="append", help="relay URL (repeatable)")
    p.set_defaults(func=cmd_lookup)

    p = sub.add_parser("seed", help="publish latest-version claims for a package list")
    p.add_argument("--packages", default="seed/packages.toml")
    p.add_argument("--key-env", help="environment variable holding the signing key (hex)")
    p.add_argument("--valid-hours", type=int, default=24)
    p.add_argument("--refresh-hours", type=int, default=6,
                   help="republish when the current claim expires within this many hours")
    p.add_argument("--dry-run", action="store_true", help="show what would be published")
    p.add_argument("--relay", action="append", help="relay URL (repeatable)")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("mcp", help="run the MCP server on stdio, for agents")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("key", help="print this installation's public key")
    p.add_argument("--rotate", action="store_true", help="replace it with a new key")
    p.set_defaults(func=cmd_key)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
