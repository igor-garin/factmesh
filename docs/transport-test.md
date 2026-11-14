# Phase 1: transport test results

Date: 2026-11-14. Script: [`spikes/relay_test.py`](../spikes/relay_test.py).
Raw results: [`transport-test-results.json`](transport-test-results.json)
(round 1) and [`transport-test-results-2.json`](transport-test-results-2.json)
(round 2: retries with a 30 s timeout, plus five more relays).

## What was tested

Per relay, using a fresh signing key per run:

- relay info document (NIP-11): software, size limits, NIP-40 support
- publish a sample claim as kind 4242 (unassigned regular kind) and as kind
  30078 (NIP-78 application data, replaceable per `d` tag)
- content sizes 1 KB, 16 KB, 64 KB, 128 KB, 256 KB
- a burst of 20 events sent back to back
- read back on a new connection, filtered by author and by `t` tag
- an event with a 20-second NIP-40 expiry, queried again after it lapsed

Every event carried an expiry of at most one day.

## Results

| Relay | Accepts claims | Read back by tag | Burst of 20 | Max size accepted | Drops expired events |
|---|---|---|---|---|---|
| relay.primal.net | yes | yes | 20/20 | 256 KB | yes |
| relay.nostr.net | yes | yes | 20/20 | 256 KB | yes |
| relay.snort.social | yes | yes | 20/20 | 256 KB | **no** (no NIP-40) |
| nostr.oxtr.dev | yes | yes | 7/20, then `rate limited` | 256 KB | yes |
| offchain.pub | small events only | yes | 11/20, then web-of-trust rejection | 1 KB (16 KB rejected) | yes |
| nos.lol | no reply | no | no reply | n/a | n/a |
| nostr.mom | no reply | no | no reply | n/a | n/a |
| nostr.wine | no, paid signup required | no, auth required | n/a | n/a | n/a |
| relay.damus.io | connection refused (HTTP 520, 521) | n/a | n/a | n/a | n/a |
| relay.nostr.band | handshake timeout | n/a | n/a | n/a | n/a |
| nostr-pub.wellorder.net | HTTP 503 | n/a | n/a | n/a | n/a |
| relay.nostr.bg | DNS name not found | n/a | n/a | n/a | n/a |
| relay.0xchat.com | DNS name not found | n/a | n/a | n/a | n/a |

Kinds 4242 and 30078 were treated identically on every relay that accepted
writes. Tag filters (`#t`) worked on every relay that accepted writes.

## Gate

**Passed.** Four relays accept and return claims reliably (primal, nostr.net,
snort.social, oxtr.dev), and a fifth works for small claims (offchain.pub). The
gate was three.

## Findings that change the design

1. **Most relays don't work for this.** 5 of 13 were usable. Clients need a
   list of several relays, health tracking, and a way to discover new ones.
   The default list should hold at least four that passed this test.
2. **Clients must enforce expiry themselves.** snort.social returned an event
   after its expiry. `stale_after` is checked by the client on every read,
   whatever the relay does.
3. **Rate limits hit fast.** oxtr.dev cut a burst off after 7 events. Publishers,
   and above all the seed publisher, must pace their sends.
4. **New keys are throttled.** offchain.pub limits keys outside its web of
   trust. This conflicts with rotating keys per session (DESIGN principle 6):
   a fresh key is always a stranger. Needs a decision.
5. **Keep claims small.** Size limits ran from under 16 KB (offchain.pub) to
   more than 256 KB. A cap of 8 KB per claim fits every relay that accepted
   writes; a sample claim is about 0.6 KB.
6. **Silent drops exist.** nos.lol and nostr.mom (strfry) neither accepted nor
   rejected writes, even with a 30 s wait. A publisher cannot rely on getting
   a rejection, so it should confirm delivery by reading back.

## Open questions

- **Kind 4242 or 30078.** 30078 lets a publisher replace its own claim in
  place (same `d` tag), which makes updates and retractions cheap, but many
  apps share that kind. 4242 is ours alone but is not yet registered in the
  NIPs list. To be decided in the spec.
- **Why nos.lol and nostr.mom drop writes.** The likely cause is a write
  policy that ignores unknown keys or kinds. Not confirmed.
- **Info documents behind Cloudflare.** primal, nostr.net, damus and
  nostr.wine refused the NIP-11 request (HTTP 403), probably because of the
  client's user agent. It does not affect claims, but the client should send
  a plain user agent.
- **Size limits are not what relays advertise.** oxtr.dev advertises a
  128 KB message limit but accepted 256 KB.

## Limits of this test

- Both rounds ran from one machine on one network. The "second process on a
  different network" check in PLAN.md was replaced by reading back on a fresh
  connection from the same machine.
- Two runs within one hour. Relay availability changes over days and weeks; the
  client should re-check relay health continuously rather than trust this
  table.

## Follow-up: label filter

The spec (SPEC.md §5) queries with `#L` and `#t` together. Tested on the four
relays that passed: primal, nostr.net, snort.social, oxtr.dev. Each returned
the probe event for `{"kinds":[30078],"#L":["afx"],"#t":[<topic>]}` and nothing
for the same filter with a different `L` value.

**Correction (Phase 4).** That test could not show whether `#t` was applied:
only one `afx` event existed. Once a second `afx` event with another topic
existed, relay.snort.social returned it for a `#t` filter it did not match, so
snort.social applies `#L` but ignores `#t` when both are given. The other three
were not re-tested for this. Receivers re-check the topic of every event
(SPEC.md §9), so this costs bandwidth, not correctness; but on such a relay a
query returns every claim on the network, capped by `limit`, and claims for
the wanted topic can be crowded out. Health tracking should eventually prefer
relays that filter correctly.
