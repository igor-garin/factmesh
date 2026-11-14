# Seeding the network

An empty network is useless to its first user, so a seed publisher keeps
"latest version" claims for the packages in
[`seed/packages.toml`](../seed/packages.toml) on the relays.

## What it does

`afx seed` runs once and exits:

1. Reads the latest version of each listed package from npm or PyPI.
2. Asks the relays for the claims its key already has (one query).
3. Publishes a claim for each package whose version changed or whose claim
   expires within 6 hours. Claims are valid for 24 hours.
4. Sends one event per second, since relays rate-limit bursts.

Run hourly, a new release reaches the network within about an hour, and an
unchanged package is republished about once a day.

```
uv run afx seed --dry-run     # show what would be published
uv run afx seed               # publish with this installation's key
```

## Running it on GitHub Actions

[`.github/workflows/seed.yml`](../.github/workflows/seed.yml) runs it every
hour at minute 17. It needs one repository secret, `AFX_SEED_KEY`: a 64-hex
character signing key used only for seeding.

```
python3 -c "import secrets; print(secrets.token_hex(32))" | gh secret set AFX_SEED_KEY
```

The key never appears in the repository or the logs; the run prints only the
public key. Anyone can fork the repository and run their own seeder with their
own key. More seeders with different keys means more independent publishers
for each fact.

## Adding packages

Add the name to `seed/packages.toml`. PyPI names must be normalized
(lowercase, `-` instead of `_` or `.`); `afx seed` refuses the list otherwise.

## Live run, 2026-11-14

A full run from a new key published all 50 claims in about two minutes. Every
claim was confirmed by at least 3 of the 5 default relays:

| Relay | Claims held |
|---|---|
| relay.primal.net, relay.nostr.net, relay.snort.social | 50 of 50 |
| nostr.oxtr.dev | 30, then `rate limited` |
| offchain.pub | 15, then `pubkey is not in our web of trust` |

A second run an hour later published nothing (all current) and took 15 s. A
separate installation looking up seeded topics got each fact and re-checked
it against the registry.

Two things to watch once the seeder runs every hour:

- The two throttling relays may accept more from a key they have seen for a
  while. If not, the seed relies on the other three.
- Claims published together expire together, so the daily refresh is a
  burst of about 50 events. Spreading expiry times would smooth it.
