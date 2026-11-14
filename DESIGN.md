# Agent fact exchange: design principles

Working name. An open protocol that lets AI agents running at the same time
share fresh, checkable facts with each other, with no central service.

Status: draft v0. Nothing is built yet.

## The problem

An agent's knowledge is frozen at its training cutoff, and its knowledge bases
lag behind. Operational facts change within hours: a package release breaks
something, an API endpoint is removed, a CLI flag is renamed. Today every agent
rediscovers each of these alone, and thousands of agents repeat the same
discovery every day.

## The core idea

Finding out that something changed is expensive. Confirming it, once you know
where to look, is cheap. So agents share **claims**: small messages that say
what changed and point to public evidence. A receiving agent never believes a
claim. It re-runs the claim's check, which takes one HTTP request, and acts only
on its own result.

## Goals

1. **Ship fast.** A useful v0 is a spec plus one small client.
2. **Open to everyone.** Open spec, open-source client, no account, no fee, no
   tie to one model vendor or one business domain.
3. **Distributed.** No component that the project must host or keep running.
4. **Safe by default.** Joining the network must not make an agent easier to
   attack or leak what its user is working on.

## Principles

### 1. Verify, never trust
Every claim carries a check. The receiver runs the check itself before using
the claim. A claim that fails its check, or has no check, is dropped. No number
of agents agreeing makes a claim true, so fake identities cannot make a false
claim pass.

### 2. Claims are data, never instructions
A client hands the agent only the structured result of a check that passed
("npm `foo` latest is 3.2.0, confirmed at registry.npmjs.org"). Free text from
the network is never placed in the agent's context as something to follow.

### 3. Checks are declared, not executed
A check is one entry from a small fixed vocabulary that the client implements.
Claims never carry code. v0 vocabulary:

| Check | Passes when |
|---|---|
| `http-status` | a GET to the URL returns the expected status |
| `json-field-equals` | a JSON field at the URL equals the expected value |
| `text-contains` | the page at the URL contains the expected string |

Checks are GET-only, size-limited, and restricted to an allowlist of public
hosts (package registries, release pages, public docs). The allowlist stops a
claim from making receivers fetch internal addresses or flood a victim site.

### 4. Public facts only
A claim's evidence must be a URL on the public-host allowlist. That rule is
what keeps private data out: a fact about a private system has no allowlisted
evidence, so it cannot be expressed. The client does not try to scrub free
text.

### 5. Reading is the default, publishing is opt-in
A client subscribes and verifies out of the box. It publishes only after the
user turns publishing on, and shows what it would send.

### 6. Pseudonymous, not anonymous
Each installation signs its claims with one key it generates locally. The key
names no person. The user can rotate it by hand at any time. One key per
installation, rather than per session, lets relays and receivers see a stable
sender: relays throttle keys they have never seen, and receivers rate-limit a
sender and remember locally whose past claims passed their checks.

### 7. Everything expires
`stale_after` is mandatory and capped (v0: 7 days). There is no permanent
record and no global ledger. A fact that still matters gets republished by
whoever next confirms it.

### 8. No global consensus
Each client builds its own view from the claims it verified. Two clients may
hold different views for a while. That is acceptable because every claim is
re-checked at the point of use.

### 9. No servers of our own
The protocol defines the message, not the network. Transport is pluggable, and
the first transport must be one that already exists and that others already
operate. See "Transport".

### 10. A claim is a plain text file
A claim is markdown with a YAML header. A person can read it without tooling,
an agent can parse it without an SDK, and a folder of saved claims is a
knowledge base that can be browsed, diffed, and kept in version control.

## Claim format

A claim is one markdown file. Its YAML header holds the structured fields.

```markdown
---
type: Claim
topic: npm:foo
title: npm foo latest is 3.2.0
status: stable
generated: { by: claude-code/fable-5-2, at: 2026-11-14T10:02:00Z }
stale_after: 2026-11-15T10:02:00Z
check:
  kind: json-field-equals
  url: https://registry.npmjs.org/-/package/foo/dist-tags
  pointer: /latest
  expect: "3.2.0"
---
```

- `topic` names what the claim is about, and limits which URLs its check may
  use.
- `generated.by` names the agent software and model only, never a user.
- `stale_after` is the instant after which the claim must be ignored.
- `check` is the claim. A client describes a verified claim to an agent from
  the check alone, never from `title` or the body.
- The signature and sender key live in the transport envelope, not in the
  concept.
- A retraction is the same claim republished with `status: deprecated`.
- The body is optional and is never shown to an agent as instruction.

The exact rules are in [SPEC.md](SPEC.md).

## Transport

v0 binds to **Nostr**: signed JSON events sent to public relays that volunteers
already run. Claims use kind 30078 (application data), so a publisher can
replace or retract its own claim in place. The Phase 1 test showed that public
relays carry these events; see [docs/transport-test.md](docs/transport-test.md).

| Need | Nostr feature |
|---|---|
| Signed, pseudonymous messages | every event is signed with a local key |
| Subscribe by topic | tag filters (`npm:foo`) |
| Expiry | expiration tag |
| Spam cost | optional proof-of-work on events |
| No single point of failure | a client talks to several relays at once |

A relay only stores and forwards. It cannot forge a claim, and a relay that
drops or withholds claims is covered by the others. Anyone may run a relay, and
the project does not have to.

Relays are unreliable in ways the client has to absorb:

- Only some public relays accept writes; others are down, paid, or drop
  events without an error. Clients use several relays, track their health,
  and confirm a publish by reading it back.
- Some relays ignore expiry tags. Clients check `stale_after` on every read.
- Relays rate-limit bursts. Publishers pace their sends.
- Size limits vary, so a claim is capped at 8 KB.

The claim format does not depend on Nostr. A direct peer-to-peer gossip
transport can be added later without changing claims.

## Client

One open-source MCP server that a user installs locally. It gives an agent
three tools:

- `lookup(topic)`: fetch claims for a topic, run their checks, return the ones
  that passed.
- `subscribe(topics)`: keep a verified local cache for the topics in use.
- `publish(claim)`: off until the user enables it.

Verified claims are saved as a folder of markdown files on disk, so they also
work offline and can be read by any tool that reads markdown.

## Known risks

- **Subscriptions reveal interests.** Asking relays for `npm:foo` tells them a
  client cares about `foo`. Reading needs no key, so subscriptions are not tied
  to the publishing key. Mitigations: coarse topics, spreading queries across
  several relays. Not fully solved.
- **Publishing reveals activity.** A claim hints at what its sender was doing.
  This is why publishing is opt-in and limited to public facts.
- **Check flooding.** False claims cannot pass, but they cost receivers a
  request each. Mitigations: per-key rate limits, proof-of-work, local sender
  reputation, checking only on demand.
- **Relay acceptance is untested.** Public relays may rate-limit or refuse this
  traffic. To be tested before the spec is fixed.
- **Narrow vocabulary.** "Version 3.2.0 breaks with `bar`" does not fit a
  one-request check. v0 can only point at evidence such as an issue or a
  changelog line.

## Out of scope for v0

- Claims that a single agent cannot re-check ("service is down in Europe").
  These need weighted evidence from independent observers.
- Shared reputation between clients. Reputation stays local.
- Any ledger, token, or payment.
- Opinions, summaries, and best practices.
