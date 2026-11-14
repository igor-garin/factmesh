"""Claim files: parsing and the offline rules of SPEC.md §2–§4."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import yaml

MAX_CONTENT_BYTES = 8192
MAX_LIFETIME = timedelta(days=7)
MAX_DEPTH = 4
MAX_URL_LENGTH = 300
ALLOWLIST = frozenset({"registry.npmjs.org", "pypi.org", "api.github.com", "github.com"})

CHECK_FIELDS = {
    "http-status": {"kind", "url", "expect"},
    "json-field-equals": {"kind", "url", "pointer", "expect"},
    "text-contains": {"kind", "url", "expect"},
}
QUESTION_FIELDS = {
    "http-status": ("kind", "url"),
    "json-field-equals": ("kind", "url", "pointer"),
    "text-contains": ("kind", "url", "expect"),
}

_NPM_PART = r"[a-z0-9][a-z0-9._~-]{0,213}"
TOPIC_PATTERNS = {
    "npm": re.compile(rf"(?:@{_NPM_PART}/)?{_NPM_PART}"),
    "pypi": re.compile(r"[a-z0-9](?:[a-z0-9]|-(?!-))*(?<!-)"),
    "github": re.compile(r"[a-z0-9](?:[a-z0-9]|-(?!-)){0,38}/[a-z0-9._-]{1,100}"),
}
_TIME_STRING = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")


class Rejected(Exception):
    """A claim or event broke a rule. `reason` is a SPEC.md §7 code."""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Claim:
    topic: str
    check: dict
    generated_by: str
    generated_at: datetime
    stale_after: datetime
    status: str
    header: dict = field(repr=False)
    content: str = field(repr=False)

    @property
    def key(self):
        return claim_key(self.check)

    @property
    def retracted(self):
        return self.status == "deprecated"


# --- YAML -------------------------------------------------------------------

class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses repeated mapping keys."""


def _construct_mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise Rejected("bad-header", f"repeated key {key!r}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                              _construct_mapping)


def _depth(value):
    if isinstance(value, dict):
        return 1 + max((_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(v) for v in value), default=0)
    return 0


def parse_header(content):
    """Split a claim file and load its YAML header (SPEC.md §2.1)."""
    if not content.startswith("---\n"):
        raise Rejected("bad-header", "file does not start with a --- line")
    lines = content.split("\n")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise Rejected("bad-header", "no closing --- line") from None
    text = "\n".join(lines[1:end])
    try:
        for token in yaml.scan(text, Loader=yaml.SafeLoader):
            if isinstance(token, (yaml.AnchorToken, yaml.AliasToken, yaml.TagToken)):
                raise Rejected("bad-header", f"{type(token).__name__[:-5].lower()} not allowed")
        header = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as e:
        raise Rejected("bad-header", str(e).splitlines()[0]) from None
    if not isinstance(header, dict):
        raise Rejected("bad-header", "header is not a mapping")
    if _depth(header) > MAX_DEPTH:
        raise Rejected("bad-header", f"nests deeper than {MAX_DEPTH} levels")
    return header


# --- fields -----------------------------------------------------------------

def parse_time(value, name):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise Rejected("bad-field", f"{name} must be UTC with a Z suffix")
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and _TIME_STRING.fullmatch(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise Rejected("bad-field", f"{name} is not a UTC date-time")


def _fields(header):
    if header.get("type") != "Claim":
        raise Rejected("bad-field", "type is not Claim")
    for name in ("topic", "check", "generated", "stale_after"):
        if name not in header:
            raise Rejected("bad-field", f"missing {name}")
    if not isinstance(header["topic"], str):
        raise Rejected("bad-field", "topic is not a string")
    gen = header["generated"]
    if not isinstance(gen, dict) or not isinstance(gen.get("by"), str) or not gen["by"]:
        raise Rejected("bad-field", "generated.by missing")
    if "at" not in gen:
        raise Rejected("bad-field", "generated.at missing")
    generated_at = parse_time(gen["at"], "generated.at")
    stale_after = parse_time(header["stale_after"], "stale_after")
    if stale_after <= generated_at:
        raise Rejected("bad-field", "stale_after is not after generated.at")
    if stale_after - generated_at > MAX_LIFETIME:
        raise Rejected("bad-field", "lifetime over 7 days")
    status = header.get("status", "stable")
    if status not in ("stable", "deprecated"):
        raise Rejected("bad-field", f"unknown status {status!r}")
    for name, kind in (("title", str), ("tags", list), ("sources", list)):
        if name in header and not isinstance(header[name], kind):
            raise Rejected("bad-field", f"{name} has the wrong type")
    return gen["by"], generated_at, stale_after, status


def validate_topic(topic):
    prefix, sep, name = topic.partition(":")
    pattern = TOPIC_PATTERNS.get(prefix)
    if not sep or pattern is None or not pattern.fullmatch(name):
        raise Rejected("bad-topic", topic)
    if prefix == "github" and name.split("/")[1] in (".", ".."):
        raise Rejected("bad-topic", topic)
    return prefix, name


def _is_json_scalar(v):
    return v is None or isinstance(v, (str, bool, int, float))


def validate_check(check):
    if not isinstance(check, dict):
        raise Rejected("bad-check", "check is not a mapping")
    kind = check.get("kind")
    if kind not in CHECK_FIELDS:
        raise Rejected("bad-check", f"unknown kind {kind!r}")
    if set(check) != CHECK_FIELDS[kind]:
        raise Rejected("bad-check", f"fields must be exactly {sorted(CHECK_FIELDS[kind])}")
    if not isinstance(check["url"], str):
        raise Rejected("bad-check", "url is not a string")
    expect = check["expect"]
    if kind == "http-status":
        if not isinstance(expect, int) or isinstance(expect, bool) or not 100 <= expect <= 599:
            raise Rejected("bad-check", "expect must be an integer 100-599")
    elif kind == "json-field-equals":
        pointer = check["pointer"]
        if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
            raise Rejected("bad-check", "pointer is not a JSON Pointer")
        if not _is_json_scalar(expect):
            raise Rejected("bad-check", "expect must be a JSON scalar")
    elif not isinstance(expect, str) or not 8 <= len(expect) <= 200:
        raise Rejected("bad-check", "expect must be a string of 8-200 characters")


# --- URLs -------------------------------------------------------------------

def url_parts(url):
    """Return (host, path segments) or raise bad-url (SPEC.md §4.2).

    Works on the string as written: no normalization, so `..` and encoded
    characters cannot slip past the binding rules.
    """
    if len(url) > MAX_URL_LENGTH:
        raise Rejected("bad-url", f"longer than {MAX_URL_LENGTH} characters")
    if not url.startswith("https://"):
        raise Rejected("bad-url", "scheme must be https")
    if any(c in url for c in "%?#") or any(c.isspace() for c in url):
        raise Rejected("bad-url", "no %, query, fragment, or whitespace allowed")
    rest = url[len("https://"):]
    host, slash, path = rest.partition("/")
    if "@" in host or ":" in host:
        raise Rejected("bad-url", "no user information or port allowed")
    if host not in ALLOWLIST:
        raise Rejected("bad-url", f"host {host!r} not on the allowlist")
    return host, path.split("/") if slash else []


def check_binding(topic, url):
    """Raise topic-mismatch unless `url` is an allowed path for `topic` (§4.3)."""
    prefix, name = validate_topic(topic)
    host, segs = url_parts(url)
    if not segs or any(s in ("", ".", "..") for s in segs):
        raise Rejected("topic-mismatch", "empty or dot path segment")
    name_segs = name.split("/")
    n = len(name_segs)

    ok = False
    if prefix == "npm" and host == "registry.npmjs.org":
        ok = (segs == ["-", "package", *name_segs, "dist-tags"]
              or segs[:n] == name_segs and len(segs) in (n, n + 1))
    elif prefix == "pypi" and host == "pypi.org":
        ok = segs[:2] == ["pypi", name] and segs[-1] == "json" and len(segs) in (3, 4)
    elif prefix == "github" and host == "api.github.com":
        ok = segs[:3] == ["repos", *name_segs]
    elif prefix == "github" and host == "github.com":
        ok = segs[:2] == name_segs
    if not ok:
        raise Rejected("topic-mismatch", f"{url} is not allowed for {topic}")


# --- whole claim ------------------------------------------------------------

def claim_key(check):
    """First 32 hex chars of SHA-256 over the check's question fields (§5.1)."""
    names = QUESTION_FIELDS.get(check.get("kind"), ("kind", "url"))
    q = {k: check[k] for k in names if k in check}
    canon = json.dumps(q, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:32]


def parse_claim(content):
    """Parse and statically validate a claim file (SPEC.md §7 steps 5-11)."""
    if len(content.encode()) > MAX_CONTENT_BYTES:
        raise Rejected("too-large", f"over {MAX_CONTENT_BYTES} bytes")
    header = parse_header(content)
    by, generated_at, stale_after, status = _fields(header)
    validate_topic(header["topic"])
    validate_check(header["check"])
    url_parts(header["check"]["url"])
    check_binding(header["topic"], header["check"]["url"])
    return Claim(topic=header["topic"], check=header["check"], generated_by=by,
                 generated_at=generated_at, stale_after=stale_after, status=status,
                 header=header, content=content)


def claim_text(topic, kind, url, expect, pointer, hours, agent, now: datetime):
    """Build a claim file. Values are written as JSON scalars, which YAML
    reads back unchanged."""
    z = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    check = [f"  kind: {json.dumps(kind)}", f"  url: {json.dumps(url)}"]
    if pointer is not None:
        check.append(f"  pointer: {json.dumps(pointer)}")
    check.append(f"  expect: {json.dumps(expect)}")
    return ("---\ntype: Claim\n"
            f"topic: {json.dumps(topic)}\n"
            f"generated: {{ by: {json.dumps(agent)}, at: {z(now)} }}\n"
            f"stale_after: {z(now + timedelta(hours=hours))}\n"
            "check:\n" + "\n".join(check) + "\n---\n")
