import json
import stat
from datetime import datetime, timedelta, timezone

import pytest

from afx import cli, keys
from afx.check import Response, is_public
from afx.claim import parse_claim
from afx.event import Signer
from afx.reputation import Reputation
from afx.store import Store
from afx.validate import validate_event

GEN = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)
NOW = GEN + timedelta(hours=2)
URL = "https://registry.npmjs.org/-/package/left-pad/dist-tags"


def claim_text(expect="1.3.0", status="stable", gen=GEN, topic="npm:left-pad", url=URL):
    z = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (f"---\ntype: Claim\ntopic: {topic}\nstatus: {status}\n"
            f"generated: {{ by: test/0, at: {z(gen)} }}\n"
            f"stale_after: {z(gen + timedelta(days=1))}\n"
            f"check:\n  kind: json-field-equals\n  url: {url}\n"
            f"  pointer: /latest\n  expect: \"{expect}\"\n---\n")


class OneResponse:
    def __init__(self, body):
        self.body = body

    def resolve(self, host):
        return ["104.16.0.35"]

    def get(self, url, address):
        return Response(200, {}, self.body.encode())


@pytest.fixture(autouse=True)
def afx_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AFX_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_sign_then_validate_round_trip():
    claim = parse_claim(claim_text())
    ev = Signer().claim_event(claim, int(GEN.timestamp()))
    assert validate_event(ev, NOW, OneResponse('{"latest":"1.3.0"}')).result == "pass"
    assert validate_event(ev, NOW, OneResponse('{"latest":"2.0.0"}')).result == "fail"


@pytest.mark.parametrize("address, public", [
    ("104.16.0.35", True), ("10.0.0.5", False), ("127.0.0.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("::1", False),
    ("::ffff:10.0.0.5", False), ("2606:4700::6810:84e5", True), ("224.0.0.1", False),
])
def test_is_public(address, public):
    assert is_public(address) is public


def test_key_created_private_and_stable(afx_home):
    a = keys.load_signer()
    mode = stat.S_IMODE((afx_home / "key").stat().st_mode)
    assert mode == 0o600
    assert keys.load_signer().pubkey == a.pubkey
    assert keys.rotate().pubkey != a.pubkey


def test_store_keeps_newest_per_publisher():
    store, signer = Store(), Signer()
    old = parse_claim(claim_text("1.3.0"))
    new = parse_claim(claim_text("1.4.0", gen=GEN + timedelta(minutes=30)))
    ev_old = signer.claim_event(old, int(old.generated_at.timestamp()))
    ev_new = signer.claim_event(new, int(new.generated_at.timestamp()))

    assert store.put(ev_new, new, NOW)
    assert not store.put(ev_old, old, NOW), "older event must not replace newer"
    [entry] = store.entries("npm:left-pad")
    assert entry["check"]["expect"] == "1.4.0"

    other = Signer().claim_event(old, int(old.generated_at.timestamp()))
    assert store.put(other, old, NOW)
    assert len(store.entries("npm:left-pad")) == 2


def test_store_md_file_is_the_claim():
    store, signer = Store(), Signer()
    claim = parse_claim(claim_text())
    store.put(signer.claim_event(claim, int(GEN.timestamp())), claim, NOW)
    [md] = list(store.root.rglob("*.md"))
    assert md.read_text() == claim.content
    assert md.parent.name == "left-pad" and md.parent.parent.name == "npm"


def test_store_scoped_topic_path():
    store = Store()
    claim = parse_claim(claim_text(topic="npm:@types/node",
                                   url="https://registry.npmjs.org/-/package/@types/node/dist-tags"))
    store.put(Signer().claim_event(claim, int(GEN.timestamp())), claim, NOW)
    assert len(store.entries("npm:@types/node")) == 1
    assert store.entries("npm:left-pad") == []


def test_store_retract_and_prune():
    store, signer = Store(), Signer()
    claim = parse_claim(claim_text())
    store.put(signer.claim_event(claim, int(GEN.timestamp())), claim, NOW)

    later = GEN + timedelta(minutes=5)
    retraction = parse_claim(claim_text(status="deprecated", gen=later))
    assert retraction.key == claim.key
    assert store.retract(signer.claim_event(retraction, int(later.timestamp())), retraction)
    assert store.entries() == []

    store.put(signer.claim_event(claim, int(GEN.timestamp())), claim, NOW)
    assert store.prune(NOW) == 0
    assert store.prune(claim.stale_after) == 1
    assert store.entries() == []


def test_reputation_rate_limit_and_counts(tmp_path):
    rep = Reputation(tmp_path / "rep.json", per_minute=3)
    assert [rep.allow("k", t) for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert rep.allow("k", 61)
    assert rep.allow("other", 3)
    for r in ("pass", "fail", "error", "error"):
        rep.record("k", r)
    rep.save()
    saved = json.loads((tmp_path / "rep.json").read_text())
    assert saved["k"] == {"pass": 1, "fail": 1, "rejected": 0, "retracted": 0}


def test_cli_rejects_without_network(tmp_path, capsys):
    bad = tmp_path / "bad.md"
    bad.write_text(claim_text(url="https://example.com/x"))
    assert cli.main(["check", str(bad), "--now", "2026-11-14T12:00:00Z"]) == 2
    assert capsys.readouterr().out.startswith("rejected: bad-url")


def test_cli_sign_produces_valid_event(tmp_path, capsys):
    f = tmp_path / "c.md"
    f.write_text(claim_text())
    assert cli.main(["sign", str(f)]) == 0
    ev = json.loads(capsys.readouterr().out)
    assert validate_event(ev, NOW, OneResponse('{"latest":"1.3.0"}')).result == "pass"


def test_cli_refuses_to_sign_invalid(tmp_path, capsys):
    f = tmp_path / "c.md"
    f.write_text(claim_text(topic="npm:Left-Pad"))
    assert cli.main(["sign", str(f)]) == 2
    assert "bad-topic" in capsys.readouterr().err
