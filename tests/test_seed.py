import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from afx import cli
from afx.check import Response
from afx.event import Signer
from afx.receiver import Receiver
from afx.relay import RelayHealth, RelayPool
from afx.reputation import Reputation
from afx.seed import load_targets, run_seed
from afx.store import Store

from fake_relay import FakeRelay

NOW = datetime.now(timezone.utc).replace(microsecond=0)


class Registries:
    """Fake npm and PyPI. `versions` maps topic -> latest version."""

    def __init__(self, versions):
        self.versions = versions

    def resolve(self, host):
        return ["104.16.0.35"]

    def get(self, url, address):
        for topic, v in self.versions.items():
            kind, name = topic.split(":", 1)
            if kind == "npm" and url.endswith(f"/-/package/{name}/dist-tags"):
                return Response(200, {}, json.dumps({"latest": v}).encode())
            if kind == "pypi" and url == f"https://pypi.org/pypi/{name}/json":
                return Response(200, {}, json.dumps({"info": {"version": v}}).encode())
        return Response(404, {}, b"{}")


@pytest.fixture
def packages(tmp_path):
    p = tmp_path / "packages.toml"
    p.write_text('npm = ["left-pad", "@types/node"]\npypi = ["requests"]\n')
    return p


def run(relay, targets, registry, signer, tmp_path, **kw):
    pool = RelayPool([relay.url], RelayHealth(tmp_path / "h.json"), timeout=1)
    return run_seed(targets, pool, signer, registry, interval=0, **kw)


def test_load_targets(packages):
    t = load_targets(packages)
    assert [x.topic for x in t] == ["npm:left-pad", "npm:@types/node", "pypi:requests"]
    assert t[1].url == "https://registry.npmjs.org/-/package/@types/node/dist-tags"


def test_load_targets_rejects_bad_names(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('pypi = ["Requests"]\n')
    with pytest.raises(ValueError, match="pypi:Requests"):
        load_targets(p)


def test_shipped_package_list_is_valid():
    assert len(load_targets("seed/packages.toml")) >= 40


def test_publishes_then_only_what_changed(tmp_path, packages):
    versions = {"npm:left-pad": "1.3.0", "npm:@types/node": "22.10.1", "pypi:requests": "2.34.2"}
    reg = Registries(versions)
    signer = Signer()
    targets = load_targets(packages)

    async def go():
        relay = await FakeRelay().start()
        first = await run(relay, targets, reg, signer, tmp_path, now=NOW)
        second = await run(relay, targets, reg, signer, tmp_path, now=NOW + timedelta(hours=1))
        versions["pypi:requests"] = "2.35.0"
        third = await run(relay, targets, reg, signer, tmp_path, now=NOW + timedelta(hours=2))
        fourth = await run(relay, targets, reg, signer, tmp_path, now=NOW + timedelta(hours=19))
        await relay.stop()
        return first, second, third, fourth, relay

    first, second, third, fourth, relay = asyncio.run(go())
    assert [(t, v) for t, v, _ in first.published] == [
        ("npm:left-pad", "1.3.0"), ("npm:@types/node", "22.10.1"), ("pypi:requests", "2.34.2")]
    assert all(s == {next(iter(s)): "confirmed"} for _, _, s in first.published)
    assert second.published == [] and len(second.fresh) == 3
    assert [(t, v) for t, v, _ in third.published] == [("pypi:requests", "2.35.0")]
    assert {t for t, _, _ in fourth.published} == {"npm:left-pad", "npm:@types/node"}, \
        "claims expiring within 6 hours are refreshed"
    assert len(relay.events) == 3, "kind 30078 keeps one event per key and question"


def test_dry_run_sends_nothing(tmp_path, packages):
    async def go():
        relay = await FakeRelay().start()
        rep = await run(relay, load_targets(packages), Registries({"npm:left-pad": "1.3.0"}),
                        Signer(), tmp_path, dry_run=True, now=NOW)
        await relay.stop()
        return rep, relay

    rep, relay = asyncio.run(go())
    assert rep.would_publish == [("npm:left-pad", "1.3.0")]
    assert {t for t, _ in rep.skipped} == {"npm:@types/node", "pypi:requests"}
    assert relay.received == 0


def test_seeded_claims_are_accepted_by_a_receiver(tmp_path, packages):
    reg = Registries({"npm:left-pad": "1.3.0", "npm:@types/node": "22.10.1",
                      "pypi:requests": "2.34.2"})

    async def go():
        relay = await FakeRelay().start()
        await run(relay, load_targets(packages), reg, Signer(), tmp_path, now=NOW)
        rec = Receiver(RelayPool([relay.url], RelayHealth(tmp_path / "r.json"), timeout=1),
                       Store(tmp_path / "store"), Reputation(tmp_path / "rep.json"), reg)
        found, _ = await rec.lookup("npm:@types/node")
        await relay.stop()
        return found

    [fact] = asyncio.run(go())
    assert fact["expect"] == "22.10.1" and fact["publishers"] == 1


def test_cli_key_env(tmp_path, packages, monkeypatch, capsys):
    monkeypatch.setenv("AFX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SEED_KEY", "zz")
    assert cli.main(["seed", "--packages", str(packages), "--key-env", "SEED_KEY",
                     "--dry-run"]) == 2
    assert "64-character hex" in capsys.readouterr().err
