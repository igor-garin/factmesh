"""Running a claim's check against the web (SPEC.md §4.4, §4.5)."""

import ipaddress
import json
from dataclasses import dataclass
from urllib.parse import urljoin

from .claim import Rejected, check_binding, url_parts

MAX_REDIRECTS = 3
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes


class FetchError(Exception):
    """No conclusion: network failure, timeout, or size limit."""


@dataclass(frozen=True)
class CheckResult:
    result: str          # "pass", "fail", or "error"
    detail: str = ""
    final_url: str = ""


def is_public(address):
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _resolve_pointer(doc, pointer):
    if pointer == "":
        return doc
    node = doc
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif (isinstance(node, list) and token.isdigit()
              and (token == "0" or not token.startswith("0")) and int(token) < len(node)):
            node = node[int(token)]
        else:
            raise KeyError(pointer)
    return node


def json_equal(a, b):
    """Strict JSON equality for scalars: bool is not a number, 1 == 1.0."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _compare(check, resp):
    kind = check["kind"]
    if kind == "http-status":
        ok = resp.status == check["expect"]
        return CheckResult("pass" if ok else "fail", f"status {resp.status}")
    if resp.status != 200:
        return CheckResult("fail", f"status {resp.status}")
    try:
        text = resp.body.decode("utf-8")
    except UnicodeDecodeError:
        return CheckResult("fail", "body is not UTF-8")
    if kind == "text-contains":
        ok = check["expect"] in text
        return CheckResult("pass" if ok else "fail", "text found" if ok else "text not found")
    try:
        doc = json.loads(text)
    except ValueError:
        return CheckResult("fail", "body is not JSON")
    try:
        value = _resolve_pointer(doc, check["pointer"])
    except KeyError:
        return CheckResult("fail", "pointer does not resolve")
    if isinstance(value, (dict, list)):
        return CheckResult("fail", "pointer refers to an object or array")
    ok = json_equal(value, check["expect"])
    return CheckResult("pass" if ok else "fail", f"value {json.dumps(value)[:80]}")


def run_check(topic, check, fetcher):
    """Run one check. `fetcher` provides resolve(host) and get(url, address).

    Exceptions other than FetchError propagate, so a test harness can stop a
    receiver that makes a request it should not.
    """
    url = check["url"]
    follow = check["kind"] != "http-status"
    for hop in range(MAX_REDIRECTS + 1):
        host, _ = url_parts(url)
        try:
            addresses = fetcher.resolve(host)
        except FetchError as e:
            return CheckResult("error", f"resolve {host}: {e}", url)
        if not addresses or not all(is_public(a) for a in addresses):
            return CheckResult("error", f"{host} resolves to a non-public address", url)
        try:
            resp = fetcher.get(url, addresses[0])
        except FetchError as e:
            return CheckResult("error", str(e), url)

        if not (follow and resp.status in REDIRECT_STATUSES):
            r = _compare(check, resp)
            return CheckResult(r.result, r.detail, url)
        if hop == MAX_REDIRECTS:
            return CheckResult("error", "too many redirects", url)
        location = resp.headers.get("location")
        if not location:
            return CheckResult("error", "redirect without location", url)
        url = urljoin(url, location)
        try:
            check_binding(topic, url)
        except Rejected as e:
            return CheckResult("error", f"redirect not allowed: {e}", url)
    raise AssertionError("unreachable")
