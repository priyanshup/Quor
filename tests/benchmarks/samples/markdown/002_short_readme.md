# retry-fetch

A tiny wrapper around `fetch` that adds exponential-backoff retries for transient network
failures, with no dependencies beyond what the platform already provides.

## Installation

```bash
npm install retry-fetch
```

## Usage

```js
import { retryFetch } from "retry-fetch";

const response = await retryFetch("https://api.example.com/data", {
  retries: 3,
  backoff: "exponential",
});
```

## Why

Most `fetch` wrappers either pull in a large dependency tree or hardcode a retry policy
that doesn't fit every use case. `retry-fetch` is deliberately small: one file, zero
dependencies, and a retry policy you configure per call rather than globally.

## Options

- `retries` — maximum number of retry attempts (default: 3)
- `backoff` — `"exponential"` or `"linear"` (default: `"exponential"`)
- `retryOn` — array of HTTP status codes that should trigger a retry (default: `[502, 503, 504]`)

## Requirements

REQ-1: Never retry a request whose method is not idempotent (`GET`, `HEAD`, `OPTIONS`)
unless the caller explicitly opts in via `retryNonIdempotent: true`.

REQ-2: Respect a `Retry-After` response header when present, instead of the configured
backoff policy.

## Known limitations

- No built-in circuit breaker — if every retry fails, the caller sees the final error.
  Pairing this with a circuit-breaker library is left to the consumer.
- Browser and Node.js `fetch` implementations both work, but this has not been tested
  against every possible `fetch` polyfill.

## TODO

TODO: add a `jitter` option so many concurrent callers backing off simultaneously don't
all retry at exactly the same moment.

## License

MIT
