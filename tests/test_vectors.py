import json
from datetime import datetime
from pathlib import Path

import pytest

from afx.check import FetchError, Response
from afx.validate import validate_event

VECTORS = json.loads(
    (Path(__file__).parent.parent / "spec" / "vectors" / "vectors.json").read_text())


class UnexpectedRequest(Exception):
    """The receiver asked for something the vector did not provide."""


class RecordedFetcher:
    def __init__(self, dns, http):
        self.dns = dns
        self.http = {r["url"]: r for r in http}
        self.requested = []

    def resolve(self, host):
        if host not in self.dns:
            raise UnexpectedRequest(f"DNS lookup for {host}")
        return self.dns[host]

    def get(self, url, address):
        self.requested.append(url)
        r = self.http.get(url)
        if r is None:
            raise UnexpectedRequest(f"GET {url}")
        if r.get("timeout"):
            raise FetchError("timeout")
        if "body_size" in r:
            raise FetchError("body over size limit")
        headers = dict(r["headers"])
        if "location" in r:
            headers["location"] = r["location"]
        return Response(r["status"], headers, r["body"].encode())


def _now(v):
    return datetime.fromisoformat(v["now"].replace("Z", "+00:00"))


@pytest.mark.parametrize("v", VECTORS["vectors"], ids=lambda v: v["name"])
def test_vector(v):
    fetcher = RecordedFetcher(v["dns"], v["http"])
    got = validate_event(v["event"], _now(v), fetcher)
    want = v["expect"]
    assert (got.result, got.reason) == (want["result"], want.get("reason", "")), got.detail
    if want["result"] in ("rejected", "retracted"):
        assert fetcher.requested == [], "no request may be made"
