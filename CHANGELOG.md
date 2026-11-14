# Changelog

## 0.1.2 (2026-11-14)

- Adds [server.json](server.json) for the official MCP registry, as
  `io.github.igor-garin/factmesh`; the README carries the registry's
  ownership marker. No code changes.

## 0.1.1 (2026-11-14)

- `subscribe` returns the current verified facts on its first call. It used
  to return only what was already stored locally, so a first call could
  look empty while claims existed. At most 20 topics per call.

## 0.1.0 (2026-11-14)

First release.

- MCP server (`uvx factmesh mcp`) with `lookup`, `subscribe`, `publish`
  (off by default), and `status`.
- Command line: `lookup`, `check`, `sign`, `publish`, `seed`, `key`.
- Protocol v0 ([SPEC.md](SPEC.md)) with 68 test vectors.
- Topics: npm packages, PyPI projects, GitHub repositories. Checks: one
  HTTPS GET to `registry.npmjs.org`, `pypi.org`, `api.github.com`, or
  `github.com`.
- Transport: Nostr kind 30078 over 5 default public relays.
- An hourly seed job publishes latest-version claims for 50 popular npm and
  PyPI packages.

Known limits are in [docs/hostile-tests.md](docs/hostile-tests.md#risks-that-remain).
