"""The network fetcher used for real checks (SPEC.md §4.4)."""

import http.client
import socket
import ssl
import time

import certifi

from . import __version__
from .check import FetchError, Response

TIMEOUT_SECONDS = 10.0
MAX_BODY_BYTES = 8 * 1024 * 1024
USER_AGENT = f"afx-client/{__version__}"


class _PinnedConnection(http.client.HTTPSConnection):
    """HTTPS to one already-checked address, with SNI and certificate checks
    for the real host name, so DNS cannot change between check and connect."""

    def __init__(self, host, address, port, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class Fetcher:
    """`port` and `context` exist for tests against a local server; checks
    always use 443 and the public CA bundle."""

    def __init__(self, timeout=TIMEOUT_SECONDS, max_body=MAX_BODY_BYTES, *,
                 port=443, context=None):
        self.timeout = timeout
        self.max_body = max_body
        self.port = port
        # certifi because python.org builds on macOS ship without a CA bundle.
        self.context = context or ssl.create_default_context(cafile=certifi.where())

    def resolve(self, host):
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise FetchError(f"DNS: {e}") from None
        return list(dict.fromkeys(info[4][0] for info in infos))

    def get(self, url, address):
        host, _, path = url[len("https://"):].partition("/")
        deadline = time.monotonic() + self.timeout
        conn = _PinnedConnection(host, address, self.port, self.timeout, self.context)
        try:
            conn.request("GET", "/" + path, headers={
                "Host": host, "User-Agent": USER_AGENT,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
                "Accept-Encoding": "identity",
            })
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError("timeout")
            conn.sock.settimeout(remaining)
            resp = conn.getresponse()
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchError("timeout")
                conn.sock.settimeout(remaining)
                # read1: at most one recv, so the deadline is checked between
                # small reads instead of after a full 64 KiB arrives.
                chunk = resp.read1(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > self.max_body:
                    raise FetchError("body over size limit")
                chunks.append(chunk)
            headers = {k.lower(): v for k, v in resp.getheaders()}
            return Response(resp.status, headers, b"".join(chunks))
        except (OSError, http.client.HTTPException) as e:
            raise FetchError(f"{type(e).__name__}: {e}") from None
        finally:
            conn.close()
