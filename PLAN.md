# Plan: first prototype and public release

Companion to [DESIGN.md](DESIGN.md). Steps are in build order. Each phase ends
with a result that can be shown to work before the next one starts.

Running cost of everything below: zero. It needs a GitHub account and a PyPI
account, both free.

## Phase 0: decisions before any code

- [ ] **Name.** Pick the project name. Check that it is free as a GitHub repo,
      a PyPI package, and a domain (domain optional).
- [ ] **License.** Proposed: Apache-2.0 for code, CC BY 4.0 for the spec.
- [ ] **Language.** Proposed: Python, packaged with `uv`, so a user runs the
      client with one `uvx` command and installs nothing by hand.
- [ ] **Transport.** Confirm Nostr for v0 (see Phase 1, which tests it).

## Phase 1: transport test

The riskiest assumption is that public relays will carry this traffic. Test it
before writing the spec.

- [x] Write a throwaway script that signs a sample claim and sends it to five
      public relays. (`spikes/relay_test.py`, 13 relays tried)
- [ ] Read it back from a second process on a different network. (Done from
      one machine only; repeat from another network.)
- [x] Measure: which relays accept a custom event kind, the size limit, whether
      topic filters work, whether expiry is honored, how fast rate limits hit.
- [x] Record the results in `docs/transport-test.md`. Gate passed.

**Gate:** at least three relays accept and return claims reliably. If not,
switch the v0 transport before continuing, and update DESIGN.md.

## Phase 2: spec v0

- [x] Write `SPEC.md`, the one document another developer needs to build a
      compatible client:
  - claim fields, which are required, and their limits (size, 7-day expiry cap)
  - the three check kinds and exactly when each passes
  - the public-host allowlist and how it changes
  - how a claim maps onto a transport message (event kind, topic tags, expiry)
  - topic naming (`npm:foo`, `pypi:foo`, `url:<host>`)
  - retraction rules
- [x] Add `spec/vectors/`: sample claims, valid and invalid, with the expected
      result for each. Any client can test itself against them. (57 vectors;
      expectations are checked against a real validator in Phase 3.)

## Phase 3: core library

No network gossip yet. Everything here runs and is tested offline or against
real public registries.

- [x] Claim parser and validator, tested against the vectors. (64/64 pass)
- [x] Checker for the three check kinds, with the safety rules built in:
      HTTPS only, allowlisted hosts only, GET only, no redirect off the
      allowlist, response size and time limits.
- [x] Local key generation and claim signing.
- [x] Local store: verified claims saved as a folder of markdown files, expired
      ones removed.
- [x] Per-sender rate limit and a local record of whose claims passed.

**Result:** a command that takes a claim file and prints pass, fail, or
rejected, with the reason.

## Phase 4: network layer

- [x] Publish a signed claim to several relays at once, confirmed by read-back.
- [x] Subscribe by topic, drop duplicates, verify signatures.
- [x] Reconnect and fall back when a relay is down.

**Result:** two machines exchange a claim, and the receiver verifies it.
(Done between separate installations on one machine over public relays;
repeat across two machines.)

## Phase 5: agent client (MCP server)

- [x] `lookup(topic)`: fetch, check, return only claims that passed, as
      structured data.
- [x] `subscribe(topics)`: keep the local cache fresh.
- [x] `publish(claim)`: disabled until the user turns it on; shows the claim
      before sending.
- [x] Config file: relays, publishing on or off, agent name. (Key rotation
      is `afx key --rotate`; the allowlist stays in code for v0.)

**Result:** a Claude Code session asks "what is the latest version of X" and
gets an answer that arrived over the network and was checked locally.

## Phase 6: hostile-input tests

Run before anything is public. Each case must be rejected.

- [ ] Claim text that tries to instruct the agent.
- [ ] Check URL pointing at a private or local address.
- [ ] Allowlisted URL that redirects off the allowlist.
- [ ] Oversized claim, oversized response, slow response.
- [ ] Expired claim, expiry beyond the cap, bad signature, replayed claim.
- [ ] A flood of claims from one key, and from many keys.

## Phase 7: seed the network

An empty network is useless to its first user, so facts must be there on day
one.

- [ ] Write a small seed publisher: it watches new releases of the most-used
      npm and PyPI packages and publishes a claim for each.
- [ ] Run it as a scheduled GitHub Actions job in the public repo. It is free,
      and anyone can fork it and run a second copy.

## Phase 8: public release

- [ ] `git init`, first commit, public GitHub repository.
- [ ] `README.md`: what it is in three sentences, a one-line install, a
      30-second demo, the safety model, how to turn publishing on.
- [ ] `LICENSE`, `SECURITY.md` (how to report a vulnerability),
      `CONTRIBUTING.md`.
- [ ] CI on every push: tests, spec vectors, hostile-input tests.
- [ ] Publish the client to PyPI. Confirm a clean machine can run it with one
      command.
- [ ] Document the one-line setup for Claude Code and at least one other agent
      host, so the project is visibly not tied to one vendor.
- [ ] Tag `v0.1.0` and write release notes that state the known risks plainly.
- [ ] Submit to the public MCP server directories.
- [ ] Announce: a short post explaining the idea, linked from Hacker News,
      relevant Reddit and Discord communities, and Nostr itself.

## Phase 9: after launch

- [ ] Triage issues weekly. Security reports first.
- [ ] Grow the host allowlist and check vocabulary through pull requests to
      the spec.
- [ ] Encourage a second, independent client in another language. A protocol
      with one implementation is still a product.
- [ ] Start on v1 items from DESIGN.md: claims no single agent can re-check,
      and a direct peer-to-peer transport.

## What only the project owner can do

- Choose the name and license.
- Create the GitHub repository and the PyPI project, and hold their
  credentials.
- Approve the first public push and the announcement.
