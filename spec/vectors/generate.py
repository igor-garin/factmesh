# /// script
# requires-python = ">=3.12"
# dependencies = ["coincurve==21.0.0"]
# ///
"""Generate the test vectors in vectors.json.

Output is deterministic: a fixed test key, fixed timestamps, and zero
auxiliary randomness for signatures, so rerunning produces identical files.

Usage: uv run spec/vectors/generate.py
"""

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from coincurve import PrivateKey

KEY = PrivateKey(hashlib.sha256(b"afx test vector key - not secret").digest())
PUBKEY = KEY.public_key_xonly.format().hex()
AUX = bytes(32)

PUBLIC_IP = "104.16.0.35"
ALLOWLIST = ["api.github.com", "github.com", "pypi.org", "registry.npmjs.org"]


def ts(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


NOW = "2026-11-14T12:00:00Z"
GEN = "2026-11-14T10:00:00Z"
STALE = "2026-11-15T10:00:00Z"


class T(str):
    """A timestamp, emitted unquoted."""


BASE = {
    "type": "Claim",
    "topic": "npm:left-pad",
    "title": "npm left-pad latest is 1.3.0",
    "status": "stable",
    "generated": {"by": "afx-vectors/0", "at": T(GEN)},
    "stale_after": T(STALE),
    "check": {
        "kind": "json-field-equals",
        "url": "https://registry.npmjs.org/-/package/left-pad/dist-tags",
        "pointer": "/latest",
        "expect": "1.3.0",
    },
}

QUESTION_FIELDS = {
    "http-status": ["kind", "url"],
    "json-field-equals": ["kind", "url", "pointer"],
    "text-contains": ["kind", "url", "expect"],
}


def scalar(v):
    if isinstance(v, T):
        return str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    raise TypeError(v)


def header(fields):
    lines = []
    for k, v in fields.items():
        if k == "check" and isinstance(v, dict):
            lines.append("check:")
            lines += [f"  {ck}: {scalar(cv)}" for ck, cv in v.items()]
        elif isinstance(v, dict):
            inner = ", ".join(f"{ik}: {scalar(iv)}" for ik, iv in v.items())
            lines.append(f"{k}: {{ {inner} }}")
        else:
            lines.append(f"{k}: {scalar(v)}")
    return "\n".join(lines)


def content(fields, body="Test vector. Never shown to an agent.\n"):
    return f"---\n{header(fields)}\n---\n{body}"


def claim_key(check):
    q = {k: check[k] for k in QUESTION_FIELDS.get(check.get("kind"), ["kind", "url"])
         if k in check}
    canon = json.dumps(q, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:32]


def sign(ev):
    payload = [0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]]
    ser = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    ev["id"] = hashlib.sha256(ser.encode()).hexdigest()
    ev["sig"] = KEY.sign_schnorr(bytes.fromhex(ev["id"]), AUX).hex()
    return ev


def event(fields, *, body=None, raw=None, kind=30078, created=GEN, tags=None,
          drop_tags=(), tag_overrides=None):
    """Build a signed event. `fields` also supplies the tag values, so a
    vector that breaks the header can still carry otherwise-correct tags."""
    text = raw if raw is not None else (content(fields) if body is None
                                         else content(fields, body))
    t = {
        "d": ["d", "afx:" + claim_key(fields["check"])],
        "L": ["L", "afx"],
        "l": ["l", "claim", "afx"],
        "t": ["t", fields["topic"]],
        "expiration": ["expiration", str(ts(fields["stale_after"]))],
    }
    t.update(tag_overrides or {})
    tag_list = tags if tags is not None else [v for k, v in t.items() if k not in drop_tags]
    ev = {"pubkey": PUBKEY, "created_at": ts(created), "kind": kind,
          "tags": tag_list, "content": text}
    return sign(ev)


def with_(**changes):
    f = copy.deepcopy(BASE)
    for path, v in changes.items():
        keys = path.split("__")
        d = f
        for k in keys[:-1]:
            d = d[k]
        if v is DELETE:
            del d[keys[-1]]
        else:
            d[keys[-1]] = v
    return f


DELETE = object()


def resp(url, status=200, body="", ctype="application/json", **extra):
    r = {"url": url, "status": status, "headers": {"content-type": ctype}, "body": body}
    r.update(extra)
    return r


NPM_URL = BASE["check"]["url"]
VECTORS = []


def vec(name, description, ev, expect, http=(), dns=None, now=NOW):
    VECTORS.append({
        "name": name,
        "description": description,
        "now": now,
        "event": ev,
        "dns": dns if dns is not None else {h: [PUBLIC_IP] for h in ALLOWLIST},
        "http": list(http),
        "expect": expect,
    })


def build():
    ok_npm = [resp(NPM_URL, body='{"latest":"1.3.0","next":"1.4.0-rc.1"}')]

    # --- pass ---------------------------------------------------------------
    vec("pass-npm-dist-tag", "npm latest dist-tag equals expect.",
        event(BASE), {"result": "pass"}, ok_npm)

    f = with_(topic="pypi:requests", title="requests latest is 2.32.3",
              check={"kind": "json-field-equals",
                     "url": "https://pypi.org/pypi/requests/json",
                     "pointer": "/info/version", "expect": "2.32.3"})
    vec("pass-pypi-version", "PyPI project version via JSON Pointer.",
        event(f), {"result": "pass"},
        [resp(f["check"]["url"], body='{"info":{"name":"requests","version":"2.32.3"}}')])

    f = with_(topic="npm:@types/node", title="@types/node latest",
              check={"kind": "json-field-equals",
                     "url": "https://registry.npmjs.org/-/package/@types/node/dist-tags",
                     "pointer": "/latest", "expect": "22.10.1"})
    vec("pass-npm-scoped", "Scoped npm name appears literally in topic and URL.",
        event(f), {"result": "pass"}, [resp(f["check"]["url"], body='{"latest":"22.10.1"}')])

    f = with_(topic="github:psf/requests", title="no v99 release",
              check={"kind": "http-status",
                     "url": "https://api.github.com/repos/psf/requests/releases/tags/v99.0.0",
                     "expect": 404})
    vec("pass-http-status-404", "An endpoint that does not exist returns 404.",
        event(f), {"result": "pass"},
        [resp(f["check"]["url"], status=404, body='{"message":"Not Found"}')])

    f = with_(topic="github:psf/requests", title="release page",
              check={"kind": "text-contains",
                     "url": "https://github.com/psf/requests/releases/tag/v2.32.3",
                     "expect": "Fix a regression in proxy handling"})
    vec("pass-text-contains", "Release page contains the expected sentence.",
        event(f), {"result": "pass"},
        [resp(f["check"]["url"], ctype="text/html",
              body="<html><body><h1>v2.32.3</h1><p>Fix a regression in proxy "
                   "handling.</p></body></html>")])

    f = with_(check={"kind": "json-field-equals",
                     "url": "https://registry.npmjs.org/left-pad/latest",
                     "pointer": "/version", "expect": "1.3.0"})
    vec("pass-redirect-within-binding",
        "One redirect to another URL that satisfies the URL rules and binding.",
        event(f), {"result": "pass"},
        [resp(f["check"]["url"], status=302, body="",
              location="https://registry.npmjs.org/left-pad/1.3.0"),
         resp("https://registry.npmjs.org/left-pad/1.3.0",
              body='{"name":"left-pad","version":"1.3.0"}')])

    # --- fail ---------------------------------------------------------------
    vec("fail-npm-value-differs", "The registry reports a different version.",
        event(BASE), {"result": "fail"},
        [resp(NPM_URL, body='{"latest":"1.4.0"}')])

    f = with_(check={**BASE["check"], "expect": 1})
    vec("fail-strict-type", "JSON equality is strict: \"1\" is not 1.",
        event(f), {"result": "fail"}, [resp(NPM_URL, body='{"latest":"1"}')])

    vec("fail-not-json", "A JSON check on a body that is not JSON fails.",
        event(BASE), {"result": "fail"},
        [resp(NPM_URL, ctype="text/html", body="<html>maintenance</html>")])

    vec("fail-pointer-missing", "A pointer that does not resolve fails.",
        event(BASE), {"result": "fail"}, [resp(NPM_URL, body='{"next":"1.4.0"}')])

    f = with_(check={**BASE["check"], "pointer": ""})
    vec("fail-pointer-to-object", "A pointer to an object never passes.",
        event(f), {"result": "fail"}, [resp(NPM_URL, body='{"latest":"1.3.0"}')])

    f = with_(topic="github:psf/requests",
              check={"kind": "http-status",
                     "url": "https://api.github.com/repos/psf/requests", "expect": 200})
    vec("fail-http-status-no-redirect",
        "http-status does not follow redirects: a 301 is the result.",
        event(f), {"result": "fail"},
        [resp(f["check"]["url"], status=301, body="",
              location="https://api.github.com/repos/psf/requests2")])

    # --- error --------------------------------------------------------------
    vec("error-timeout", "No response within 10 s: no conclusion.",
        event(BASE), {"result": "error"}, [resp(NPM_URL, timeout=True)])

    f = with_(topic="github:psf/requests",
              check={"kind": "json-field-equals",
                     "url": "https://api.github.com/repos/psf/requests",
                     "pointer": "/archived", "expect": False})
    vec("error-redirect-breaks-binding",
        "A redirect to a URL outside the topic binding gives error, not fail.",
        event(f), {"result": "error"},
        [resp(f["check"]["url"], status=301, body="",
              location="https://api.github.com/repositories/1362490")])

    vec("error-private-address",
        "An allowlisted host that resolves to a private address is not contacted.",
        event(BASE), {"result": "error"}, [],
        dns={"registry.npmjs.org": ["10.0.0.5"]})

    vec("error-body-too-large", "A body over 8 MiB gives error.",
        event(BASE), {"result": "error"}, [resp(NPM_URL, body_size=9 * 1024 * 1024)])

    # --- retracted ----------------------------------------------------------
    vec("retracted", "status: deprecated retracts without running the check.",
        event(with_(status="deprecated")), {"result": "retracted"})

    # --- rejected -----------------------------------------------------------
    def rej(name, description, ev, reason, now=NOW):
        vec(name, description, ev, {"result": "rejected", "reason": reason}, now=now)

    rej("reject-wrong-kind", "Kind 1 is not a claim.", event(BASE, kind=1), "wrong-kind")

    ev = event(BASE)
    ev["sig"] = ("0" if ev["sig"][0] != "0" else "1") + ev["sig"][1:]
    rej("reject-bad-signature", "One signature character changed.", ev, "bad-signature")

    ev = event(BASE)
    ev["content"] = ev["content"].replace("1.3.0", "1.3.1")
    rej("reject-content-changed", "Content changed after signing.", ev, "bad-signature")

    rej("reject-missing-label", "No L tag.", event(BASE, drop_tags=("L",)), "not-a-claim")
    rej("reject-unknown-version", "l tag of a version this client does not implement.",
        event(BASE, tag_overrides={"l": ["l", "claim/1", "afx"]}), "not-a-claim")

    rej("reject-too-large", "Content over 8192 bytes.",
        event(BASE, body="x" * 8300), "too-large")

    rej("reject-no-header", "Content does not start with ---.",
        event(BASE, raw=header(BASE) + "\n---\n"), "bad-header")
    rej("reject-yaml-alias", "YAML anchors and aliases are not allowed.",
        event(BASE, raw=content(BASE).replace('title: "npm left-pad latest is 1.3.0"',
                                              "title: &t npm left-pad\ntags: [*t]")),
        "bad-header")
    rej("reject-yaml-tag", "Explicit YAML type tags are not allowed.",
        event(BASE, raw=content(BASE).replace('title: "npm left-pad latest is 1.3.0"',
                                              'title: !!binary aGVsbG8=')),
        "bad-header")

    rej("reject-wrong-type", "type is not Claim.", event(with_(type="Note")), "bad-field")
    rej("reject-missing-stale-after", "stale_after is required.",
        event(BASE, raw=content(with_(stale_after=DELETE))), "bad-field")
    rej("reject-missing-generated-by", "generated.by is required.",
        event(with_(generated={"at": T(GEN)})), "bad-field")
    rej("reject-lifetime-over-7-days", "stale_after more than 7 days after generated.at.",
        event(with_(stale_after=T("2026-11-21T10:00:01Z"))), "bad-field")
    rej("reject-stale-before-generated", "stale_after not after generated.at.",
        event(with_(stale_after=T(GEN))), "bad-field")

    rej("reject-unknown-topic-prefix", "maven: is not a v0 topic.",
        event(with_(topic="maven:junit")), "bad-topic")
    rej("reject-topic-uppercase", "Topics are lowercase.",
        event(with_(topic="npm:Left-Pad")), "bad-topic")
    rej("reject-pypi-not-normalized", "PyPI names must be PEP 503 normalized.",
        event(with_(topic="pypi:foo_bar")), "bad-topic")

    rej("reject-unknown-check-kind", "regex is not a check kind.",
        event(with_(check={"kind": "regex", "url": NPM_URL, "expect": "1\\..*"})), "bad-check")
    rej("reject-extra-check-field", "Check fields beyond those of its kind.",
        event(with_(check={**BASE["check"], "headers": {"x": "y"}})), "bad-check")
    rej("reject-text-expect-too-short", "text-contains expect under 8 characters.",
        event(with_(topic="github:psf/requests",
                    check={"kind": "text-contains",
                           "url": "https://github.com/psf/requests/releases",
                           "expect": "v2"})), "bad-check")
    rej("reject-json-expect-object", "json-field-equals expect must be a scalar.",
        event(with_(check={**BASE["check"], "expect": {"a": 1}})), "bad-check")
    rej("reject-status-out-of-range", "http-status expect outside 100-599.",
        event(with_(check={"kind": "http-status", "url": NPM_URL, "expect": 999})),
        "bad-check")

    def bad_url(name, description, url, reason="bad-url"):
        rej(name, description, event(with_(check={**BASE["check"], "url": url})), reason)

    bad_url("reject-http-scheme", "Plain http.",
            "http://registry.npmjs.org/-/package/left-pad/dist-tags")
    bad_url("reject-port", "Explicit port.",
            "https://registry.npmjs.org:8443/-/package/left-pad/dist-tags")
    bad_url("reject-query", "Query string.",
            "https://registry.npmjs.org/-/package/left-pad/dist-tags?x=1")
    bad_url("reject-fragment", "Fragment.",
            "https://registry.npmjs.org/-/package/left-pad/dist-tags#latest")
    bad_url("reject-percent", "Percent-encoding.",
            "https://registry.npmjs.org/-/package/left%2dpad/dist-tags")
    bad_url("reject-userinfo", "User information.",
            "https://user@registry.npmjs.org/-/package/left-pad/dist-tags")
    bad_url("reject-host-not-allowed", "Host not on the allowlist.",
            "https://example.com/-/package/left-pad/dist-tags")
    bad_url("reject-topic-mismatch", "URL for a different package than the topic.",
            "https://registry.npmjs.org/-/package/evil-pad/dist-tags", "topic-mismatch")
    bad_url("reject-dot-segment", "Path with a .. segment.",
            "https://registry.npmjs.org/left-pad/../evil-pad", "topic-mismatch")
    bad_url("reject-wrong-host-for-topic", "github.com URL under an npm topic.",
            "https://github.com/left-pad/left-pad/releases", "topic-mismatch")
    rej("reject-github-other-repo", "github.com URL for another repository.",
        event(with_(topic="github:psf/requests",
                    check={"kind": "text-contains",
                           "url": "https://github.com/pallets/flask/releases",
                           "expect": "Release notes"})), "topic-mismatch")

    rej("reject-t-tag-mismatch", "t tag differs from topic.",
        event(BASE, tag_overrides={"t": ["t", "npm:react"]}), "tag-mismatch")
    rej("reject-expiration-mismatch", "expiration tag differs from stale_after.",
        event(BASE, tag_overrides={"expiration": ["expiration", str(ts(STALE) + 86400)]}),
        "tag-mismatch")
    rej("reject-d-tag-mismatch", "d tag is not the claim key.",
        event(BASE, tag_overrides={"d": ["d", "afx:" + "0" * 32]}), "tag-mismatch")
    rej("reject-duplicate-t-tag", "A required tag appears twice.",
        event(BASE, tags=[["d", "afx:" + claim_key(BASE["check"])], ["L", "afx"],
                          ["l", "claim", "afx"], ["t", "npm:left-pad"], ["t", "npm:react"],
                          ["expiration", str(ts(STALE))]]), "tag-mismatch")

    rej("reject-created-far-from-generated", "created_at an hour after generated.at.",
        event(BASE, created="2026-11-14T11:00:00Z"), "bad-time")
    rej("reject-generated-in-future", "generated.at more than 300 s after now.",
        event(BASE), "bad-time", now="2026-11-14T09:50:00Z")
    rej("reject-expired", "now is at or after stale_after.",
        event(BASE), "expired", now=STALE)


def main():
    build()
    out = Path(__file__).with_name("vectors.json")
    out.write_text(json.dumps({"spec_version": "0", "pubkey": PUBKEY,
                               "vectors": VECTORS}, indent=2, ensure_ascii=False) + "\n")
    counts = {}
    for v in VECTORS:
        counts[v["expect"]["result"]] = counts.get(v["expect"]["result"], 0) + 1
    print(f"wrote {len(VECTORS)} vectors to {out}: {counts}")


if __name__ == "__main__":
    main()
