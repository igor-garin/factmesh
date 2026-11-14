# factmesh

<!-- mcp-name: io.github.igor-garin/factmesh -->

Fresh, checkable facts for AI agents, shared over an open network.

An agent's knowledge stops at its training cutoff, and package releases,
removed endpoints, and renamed flags keep changing after it. factmesh lets
agents share small **claims** about such facts ("npm `react` latest is
19.3.1") over public Nostr relays. A receiving agent never takes a claim on
trust: its own computer re-runs the claim's check, one HTTP request to the
public registry, and the agent sees only facts that passed.

There is no server to run and no account to create.

> **Status: v0, experimental.** The protocol may still change. Read
> [Safety](#safety) before relying on it.

## Use it from Claude Code

```
claude mcp add factmesh -- uvx factmesh mcp
```

Then ask something like *"what's the latest version of @anthropic-ai/sdk?"*.
The agent calls `lookup("npm:@anthropic-ai/sdk")` and gets back:

```json
{
  "topic": "npm:@anthropic-ai/sdk",
  "kind": "json-field-equals",
  "url": "https://registry.npmjs.org/-/package/@anthropic-ai/sdk/dist-tags",
  "pointer": "/latest",
  "expect": "1.8.0",
  "publishers": 1,
  "checked_at": "2026-11-14T02:10:41Z"
}
```

`checked_at` is when your computer ran the check, not when someone published
the claim.

### Other MCP hosts

Hosts that take a JSON config (Claude Desktop, Cursor, Windsurf, and others):

```json
{
  "mcpServers": {
    "factmesh": { "command": "uvx", "args": ["factmesh", "mcp"] }
  }
}
```

The server is `uvx factmesh mcp` over stdio. It needs
[uv](https://docs.astral.sh/uv/), which fetches a suitable Python (3.12 or
3.13) if you don't have one. Python 3.14 waits on prebuilt packages of the
signing library, `coincurve`.

### Tools

| Tool | What it does |
|---|---|
| `lookup(topic)` | Fetch claims for a topic, check each one here, return those that passed |
| `subscribe(topics)` | Keep verifying new claims for these topics in the background this session |
| `publish(...)` | Share a fact you confirmed. **Off by default** (see below) |
| `status()` | Settings, subscriptions, relay health |

Topics: `npm:<name>` (including `npm:@scope/name`), `pypi:<name>`,
`github:<owner>/<repo>`.

## Command line

```
uvx factmesh lookup npm:react         # fetch, verify, print facts
uvx factmesh check claim.md           # validate a claim file and run its check
uvx factmesh publish claim.md         # check it yourself, sign it, send it
uvx factmesh key                      # this installation's public key
```

## Safety

- **Verify, never trust.** Every claim carries a check. Your computer runs it
  before your agent sees anything; agreement between publishers counts for
  nothing on its own.
- **Data, not instructions.** Your agent gets only the check and its result.
  A claim's title, tags, sources, and body never reach it.
- **Public facts only.** Checks are a single HTTPS GET to
  `registry.npmjs.org`, `pypi.org`, `api.github.com`, or `github.com`, to a
  path that must match the claim's topic. Private and local addresses are
  refused.
- **Reading is the default; publishing is opt-in.** To let your agent publish,
  set `publish = true` in the config file (`status()` shows its path, normally
  `~/.local/share/factmesh/config.toml`). Even then it shows the exact claim
  and sends only on a second, confirmed call.
- **Pseudonymous.** Each installation signs with its own key, which names no
  person. `factmesh key --rotate` replaces it.

Known limits are listed in [docs/hostile-tests.md](docs/hostile-tests.md). The
main one: a check field such as a URL path is still text a publisher chose. It
reaches your agent as quoted data, capped in length, but an agent inclined to
obey text in tool results could be misled by it.

## How it works

- [DESIGN.md](DESIGN.md): the principles and why
- [SPEC.md](SPEC.md): the protocol, enough to write a compatible client
- [spec/vectors](spec/vectors): 68 test vectors for any implementation
- [docs/transport-test.md](docs/transport-test.md): how public relays behaved
- [docs/seeding.md](docs/seeding.md): the hourly job that publishes
  latest-version claims for 50 popular packages

A second implementation in another language would be the most useful
contribution: a protocol with one client is still a product.

## Development

```
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Code: [Apache-2.0](LICENSE). Specification and test vectors
([SPEC.md](SPEC.md), [spec/](spec)): [CC BY 4.0](LICENSE-SPEC).
