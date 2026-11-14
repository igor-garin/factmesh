# Contributing

## Setup

```
uv run pytest
```

Tests use local fake relays and recorded HTTP responses; they need no network.

## Changing the protocol

[SPEC.md](SPEC.md) is the contract between implementations. A change to it
needs:

1. the spec text,
2. test vectors in [`spec/vectors/generate.py`](spec/vectors/generate.py)
   (then `uv run spec/vectors/generate.py`; edit the generator, not the JSON),
3. the client change, with all vectors passing.

Widening what a claim can check (new hosts, topics, or check kinds) is the most
sensitive kind of change. Explain what a hostile publisher could do with it.

## Adding seed packages

Add names to [`seed/packages.toml`](seed/packages.toml).

## Other implementations

Clients in other languages are very welcome. Conformance means passing every
vector in [`spec/vectors`](spec/vectors).
