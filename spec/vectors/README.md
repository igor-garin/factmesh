# Test vectors

`vectors.json` holds sample events and the result a conforming receiver must
produce for each under [SPEC.md](../../SPEC.md) §7. `generate.py` produces it
deterministically:

```
uv run spec/vectors/generate.py
```

Edit the generator, not the JSON.

## Format

```json
{
  "spec_version": "0",
  "pubkey": "<hex key that signed every vector>",
  "vectors": [
    {
      "name": "pass-npm-dist-tag",
      "description": "npm latest dist-tag equals expect.",
      "now": "2026-11-14T12:00:00Z",
      "event": { "id": "...", "pubkey": "...", "created_at": 0, "kind": 30078,
                 "tags": [], "content": "...", "sig": "..." },
      "dns": { "registry.npmjs.org": ["104.16.0.35"] },
      "http": [ { "url": "...", "status": 200,
                  "headers": { "content-type": "application/json" },
                  "body": "..." } ],
      "expect": { "result": "pass" }
    }
  ]
}
```

- `now`: the current time the receiver must assume.
- `dns`: the addresses each host resolves to. A harness MUST NOT use real DNS.
- `http`: the response for each URL the check may request, including redirect
  targets. A harness MUST NOT use the real network. An entry may instead have:
  - `"timeout": true`: no response arrives within the time limit;
  - `"body_size": <n>`: a body of `n` bytes, larger than the limit, not
    included literally;
  - `"location": "<url>"`: the `Location` header of a redirect.
- `expect.result`: `pass`, `fail`, `error`, `retracted`, or `rejected`.
- `expect.reason`: for `rejected`, the reason code from SPEC.md §7.

## Running them

For each vector, a harness feeds `event` to the receiver with the clock set to
`now`, DNS answered from `dns`, and HTTP answered from `http`, then compares
the receiver's result (and reason) with `expect`.

A request for a URL that is not in `http`, or a lookup for a host that is not
in `dns`, means the receiver went further than it should have. The harness
MUST report that vector as failed.

URL rules (§4.2, §4.3) apply to the URL string as written in the claim, before
any normalization a URL library might do. `reject-dot-segment` tests this.

The signing key is derived from a fixed public string. It is a test key only;
never use it to publish.

## Coverage

| Result | Count | What they cover |
|---|---|---|
| `pass` | 6 | each check kind, scoped npm names, a redirect inside the binding |
| `fail` | 8 | wrong value, strict JSON types (string vs number, bool vs number), non-JSON body, missing pointer, pointer to an object, status other than 200, `http-status` not following redirects |
| `error` | 4 | timeout, redirect outside the binding, private address, oversized body |
| `retracted` | 1 | `status: deprecated` |
| `rejected` | 45 | every reason code in §7, including repeated YAML keys, CRLF lines, deep nesting, and non-UTC times |
