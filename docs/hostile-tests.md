# Phase 6: hostile-input tests

Tests: [`tests/test_hostile.py`](../tests/test_hostile.py), plus the
`reject-*` vectors in [`spec/vectors`](../spec/vectors). Run with `uv run pytest`.

## Attacks tested

| Attack | Result |
|---|---|
| Publisher text aimed at the agent (title, tags, sources, body) | never reaches the agent; asserted on the facts output |
| Check URL on a private or local address, or DNS answers mixing public and private | not contacted; result `error` |
| Redirects off the allowlist: other host, `http`, `//host`, userinfo, port, metadata IP, `../`, `%` | result `error`, no second request |
| Redirect loop | stops after 3 redirects |
| Certificate for another name | refused by TLS |
| Server that never sends headers, or trickles the body | `error` within the 10 s limit (tested at 1 s) |
| Endless response body | `error` at the size limit |
| Deeply nested JSON, huge numbers, invalid UTF-8, `NaN` | result `fail` |
| 4,000 randomly mutated events, most re-signed so they pass the signature check | always a result, never an exception; reach 8+ validation steps |
| Relay sending malformed messages, or one message over the size limit | relay marked failed; other relays still answer |
| 300 keys each sending a different false claim | at most 20 checks; a publisher with a passing history is still checked |
| 200 keys sending the same claim | one request |
| One event delivered by three relays | counted once |
| Old event replayed after its retraction | refused until every older event has expired |
| Forged newer event under another publisher's key | rejected; the real claim stays (Phase 4) |
| Look-alike characters in topics and hosts (Cyrillic, full-width, zero-width, trailing dot, ideographic full stop) | rejected |
| Topic names chosen to escape the store folder | files stay inside the store |

## Bugs found and fixed

1. A correctly signed event with a string `created_at` crashed validation.
   Events now pass a NIP-01 field-type check first (new reason `bad-event`).
2. JSON nested 100,000 deep crashed the check. Now a `fail`.
3. A retracted claim could be brought back by replaying the old event. The
   store keeps a tombstone for 7 days and 300 s after the retraction.
4. An event whose `pubkey` was not a string crashed a whole lookup, because
   rate limiting ran before validation. Field types are now checked first.
5. A server trickling its body could stretch a check past 10 s. Reads now take
   whatever bytes are ready, and the deadline is checked between them.

Each fix was confirmed by undoing it and seeing a test fail.

## Risks that remain

- **Publisher-chosen text in check fields.** A claim that a URL returns 404
  passes for any path, so the path can carry arbitrary text, and so can
  `expect` in a `text-contains` check. Both reach the agent as quoted data,
  limited to 300 and 200 characters. An agent that obeys instructions found in
  tool results could still be misled.
- **Spam crowding.** Many fresh keys can fill the 20-check budget so that a
  correct claim from a new publisher is not checked in that lookup. Publishers
  with a passing history keep priority, and the claim is checked once spam
  ages out, but a new honest publisher is not protected.
- **Relays that ignore the topic filter** return every claim, up to their
  limit, so a busy network can push the wanted topic out of their answers.
- **Consent to publish** is enforced by the config setting; the preview step
  relies on the agent showing it to the user.
- **Not yet reviewed by anyone else.** These tests were written by the same
  author as the code.
