# Claim exchange protocol, v0

Status: draft. Version `0`. The design and its reasons are in
[DESIGN.md](DESIGN.md); this document holds only the rules a compatible client
must follow.

The key words MUST, MUST NOT, SHOULD, and MAY are used as in RFC 2119.

The protocol namespace is `afx` (placeholder until the project is named; see
PLAN.md Phase 0). It appears in event tags (§5) and claim identifiers.

## 1. Overview

A **claim** is a small text file that states one public fact and says how to
confirm it. A **publisher** signs a claim and sends it to Nostr relays. A
**receiver** fetches claims for a topic, validates each one offline, runs its
check against the public web, and passes only checks that succeeded to its
agent.

A receiver never treats a claim as true because someone published it. The
check is the claim: what a verified claim means is defined entirely by its
check (§4) and the check result.

## 2. Claim file

A claim is UTF-8 text: a YAML header between `---` lines, then an optional
markdown body.

```markdown
---
type: Claim
topic: npm:left-pad
title: npm left-pad latest is 1.3.0
status: stable
generated: { by: claude-code/opus-5-5, at: 2026-11-14T10:02:00Z }
stale_after: 2026-11-15T10:02:00Z
check:
  kind: json-field-equals
  url: https://registry.npmjs.org/-/package/left-pad/dist-tags
  pointer: /latest
  expect: "1.3.0"
---
Optional notes for people. Never shown to an agent.
```

### 2.1 Parsing

- The file MUST start with a line `---`, and the header MUST end at the next
  line that is exactly `---`.
- The header is parsed as YAML with a safe loader. Receivers MUST reject a
  header that uses anchors, aliases, or explicit type tags (`!`), or that nests
  deeper than 4 levels.
- The whole file, header plus body, MUST NOT exceed 8192 bytes.

### 2.2 Fields

| Field | Required | Value |
|---|---|---|
| `type` | yes | the string `Claim` |
| `topic` | yes | a topic (§3) |
| `check` | yes | a check (§4) |
| `generated.by` | yes | the publishing software and model, e.g. `claude-code/opus-5-5`; MUST NOT identify a person |
| `generated.at` | yes | when the publisher last ran the check and saw it pass |
| `stale_after` | yes | the instant from which the claim MUST be ignored |
| `status` | no | `stable` (default) or `deprecated` (a retraction, §6) |
| `title` | no | a short label for people |
| `tags` | no | a list of short strings |
| `sources` | no | a list of `{ resource, title }` entries pointing at public pages |

- Times are RFC 3339 / ISO 8601 date-times in UTC with a `Z` suffix, e.g.
  `2026-11-14T10:02:00Z`. Written unquoted, YAML loaders read them as
  timestamps; quoted, as strings. Receivers MUST accept both.
- `stale_after` MUST be later than `generated.at`, and at most 7 days
  (604800 s) later.
- Receivers MUST ignore unknown fields rather than reject them.
- `title`, `tags`, `sources`, and the body are for people. Receivers MUST NOT
  pass them to an agent (§8).

## 3. Topics

A topic names what a claim is about. Receivers subscribe by topic, and the
topic limits which URLs a check may use (§4.3). Topics are lowercase.

| Topic | Form | Name rules |
|---|---|---|
| npm package | `npm:<name>` | npm package name, scoped names as `@scope/name` |
| PyPI project | `pypi:<name>` | normalized per PEP 503: lowercase, runs of `-`, `_`, `.` replaced by one `-` |
| GitHub repository | `github:<owner>/<repo>` | lowercase |

Other prefixes are reserved. Receivers MUST reject a claim whose topic does not
match one of these forms.

## 4. Checks

A check is one GET request and one comparison. Claims never carry code.

### 4.1 Kinds

| `kind` | Other fields | Passes when |
|---|---|---|
| `http-status` | `url`, `expect` (integer 100–599) | the status of the response to `url` equals `expect`. Redirects are not followed: a 3xx status is the result. |
| `json-field-equals` | `url`, `pointer`, `expect` | the response is JSON, `pointer` (RFC 6901 JSON Pointer) resolves, and the value there equals `expect` |
| `text-contains` | `url`, `expect` (string, 8–200 characters) | the response body, decoded as UTF-8, contains `expect` exactly (case-sensitive) |

For `json-field-equals`, `expect` MUST be a string, number, boolean, or null,
and equality is strict JSON equality: `"1"` does not equal `1`, and a pointer
to an object or array never passes.

A check with an unknown `kind`, or with missing or extra fields for its kind,
MUST be rejected.

### 4.2 URL rules

A check `url` MUST:

- use the `https` scheme, with no port, user information, query, or fragment;
- contain no `%` character (one spelling per resource);
- have a host on the allowlist;
- match its topic's binding (§4.3).

v0 allowlist: `registry.npmjs.org`, `pypi.org`, `api.github.com`, `github.com`.

A receiver MAY add hosts to its own allowlist. That changes only what it will
verify; it does not change the protocol.

### 4.3 Topic binding

The URL path MUST be one of the forms listed for the claim's topic, with the
topic's name substituted literally.

| Topic | Host | Allowed paths |
|---|---|---|
| `npm:<name>` | `registry.npmjs.org` | `/-/package/<name>/dist-tags`, `/<name>`, `/<name>/<version-or-tag>` |
| `pypi:<name>` | `pypi.org` | `/pypi/<name>/json`, `/pypi/<name>/<version>/json` |
| `github:<o>/<r>` | `api.github.com` | `/repos/<o>/<r>`, `/repos/<o>/<r>/<anything>` |
| `github:<o>/<r>` | `github.com` | `/<o>/<r>`, `/<o>/<r>/<anything>` |

`<version-or-tag>` and `<version>` are one path segment. `<anything>` is one
or more segments. No path segment may be `.` or `..` or empty.

Binding stops a claim filed under one topic from being confirmed by a page
about something else.

### 4.4 Fetching

When running a check, the receiver:

- MUST resolve the host and refuse to connect if any resolved address is
  loopback, private, link-local, multicast, or otherwise not globally routable,
  and MUST connect to an address it checked;
- MUST send no cookies and no credentials, and SHOULD send a user agent of the
  form `afx-client/<version>`;
- for `json-field-equals` and `text-contains`, MAY follow up to 3 redirects,
  each of which MUST satisfy §4.2 and §4.3 again;
- MUST stop after 10 seconds in total and after 8 MiB of response body.

### 4.5 Results

Running a check gives one of:

- `pass`: the condition in §4.1 held.
- `fail`: the response arrived and the condition did not hold (including a
  JSON check on a body that is not JSON, or a pointer that does not resolve).
- `error`: no conclusion: network failure, timeout, size limit, a host that
  resolves to a non-public address (§4.4), or a redirect that breaks §4.2 or
  §4.3. An `error` MUST NOT count against the publisher.

## 5. Nostr event

A claim travels as a Nostr event (NIP-01):

| Event field | Value |
|---|---|
| `kind` | `30078` |
| `content` | the claim file, exactly |
| `created_at` | within 300 s of `generated.at` |
| tag `d` | `afx:` + claim key (§5.1) |
| tag `L` | `afx` |
| tag `l` | `claim`, `afx` |
| tag `t` | the topic |
| tag `expiration` | `stale_after` as Unix seconds (NIP-40) |

Each of these tags MUST appear exactly once. Other tags MAY appear and are
ignored.

Receivers query with a filter such as:

```json
{ "kinds": [30078], "#L": ["afx"], "#t": ["npm:left-pad"] }
```

### 5.1 Claim key

The claim key identifies the question a claim answers, so that a publisher's
newer answer replaces its older one (kind 30078 keeps one event per publisher
per `d` tag).

1. Take the check's question fields: `kind` and `url`, plus `pointer` for
   `json-field-equals`, plus `expect` for `text-contains`.
2. Serialize them as JSON with keys sorted, no whitespace, UTF-8.
3. The claim key is the first 32 hex characters of the SHA-256 of that string.

## 6. Updates and retractions

- To update its answer, a publisher sends a new event with the same `d` tag.
  Relays keep the newest; receivers MUST keep only the newest `created_at` per
  publisher and `d` tag.
- To retract, a publisher sends an event with the same `d` tag and
  `status: deprecated`. A receiver drops that publisher's claim for that key
  and does not run the check.
- A publisher can retract only its own claims. A false claim from someone else
  needs no retraction: its check fails.

## 7. Validation order

A receiver MUST process an event in this order and stop at the first failure.
The reason codes are used by the test vectors.

| Step | Rejected with reason |
|---|---|
| 1. `kind` is 30078 | `wrong-kind` |
| 2. `id` and `sig` are valid (NIP-01) | `bad-signature` |
| 3. `L` and `l` tags present as in §5 | `not-a-claim` |
| 4. `content` is at most 8192 bytes | `too-large` |
| 5. header parses under §2.1 | `bad-header` |
| 6. required fields present and well-formed, including the `stale_after` rules (§2.2) | `bad-field` |
| 7. topic has a valid form (§3) | `bad-topic` |
| 8. check is well-formed (§4.1) | `bad-check` |
| 9. URL rules (§4.2) | `bad-url` |
| 10. topic binding (§4.3) | `topic-mismatch` |
| 11. `t`, `expiration`, and `d` tags match the claim (§5) | `tag-mismatch` |
| 12. `created_at` within 300 s of `generated.at` | `bad-time` |
| 13. `generated.at` no more than 300 s in the future | `bad-time` |
| 14. now is before `stale_after` | `expired` |
| 15. `status` is `deprecated` | result `retracted`, no check |
| 16. run the check (§4) | result `pass`, `fail`, or `error` |

A rejected event MUST be dropped. A receiver MAY lower its trust in a publisher
whose events are rejected or `fail`, but MUST NOT for `error`.

## 8. What an agent sees

A receiver passes an agent only facts derived from a check it ran itself and
that passed. For each, it MAY pass:

- the topic;
- the check `kind`, `url`, `pointer`, and `expect`;
- when the receiver ran the check;
- how many distinct publishers sent a claim with this claim key.

The receiver MUST NOT pass `title`, `tags`, `sources`, the body, the event's
other tags, or the publisher key. Those are controlled by the publisher; the
check result is not.

Because `expect` in a passing `text-contains` check is text the publisher
chose, receivers SHOULD present it to an agent as quoted data, never inline as
prose.

## 9. Relays

These rules are informative except where MUST is used.

- Publishers SHOULD send to at least 3 relays, SHOULD pace sends (for example
  one event per second per relay), and SHOULD confirm a publish by reading the
  event back.
- Receivers SHOULD query at least 3 relays and merge the results.
- Receivers MUST enforce `stale_after` themselves; some relays ignore
  `expiration`.

## 10. Versioning

This is version `0`. Any incompatible change to §2–§7 gets a new version
number and a new `l` tag value (`claim/1`, ...). Receivers MUST reject claims
of a version they do not implement, by treating an unknown `l` value as
`not-a-claim`.

## 11. Test vectors

`spec/vectors/` holds sample events with recorded HTTP responses and the
expected result of §7 for each. A client conforms when it gets every vector
right. See `spec/vectors/README.md`.
