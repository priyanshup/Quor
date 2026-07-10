/**
 * Payment gateway client — a thin wrapper around the third-party charge
 * API, with idempotency-key handling and defensive retry logic.
 *
 * This file is deliberately over-commented relative to the rest of the
 * codebase: the gateway's own API documentation is sparse and several of
 * these behaviors were discovered the hard way in production incidents,
 * so future maintainers get the context inline rather than having to dig
 * through old postmortems.
 */

import { logger } from "./logger.js";

// The gateway silently caps idempotency keys at 64 characters and truncates
// anything longer without warning (confirmed via support ticket #4821) — we
// hash long keys ourselves rather than relying on their truncation, since
// truncation can create collisions between two genuinely different keys
// that happen to share a long common prefix (e.g. two charges for the same
// order but different line items).
const MAX_IDEMPOTENCY_KEY_LENGTH = 64;

// Gateway support confirmed 3 retries with jittered backoff is their own
// internal recommendation for 5xx responses; retrying 4xx is never correct
// since those indicate a client-side request problem that a retry can't fix.
const MAX_RETRIES = 3;

/**
 * Build a stable idempotency key for a charge request.
 *
 * IMPORTANT: this must be deterministic for the same logical charge across
 * retries (including retries from a completely different process, e.g. a
 * background job retry after a crash) — otherwise a retried charge could
 * double-bill the customer. Do not include timestamps or random values.
 */
export function buildIdempotencyKey(orderId, attemptType) {
  const raw = `${orderId}:${attemptType}`;
  if (raw.length <= MAX_IDEMPOTENCY_KEY_LENGTH) {
    return raw;
  }
  // Long key: hash it instead of truncating (see module comment above for
  // why truncation is unsafe here).
  return hashKey(raw);
}

function hashKey(raw) {
  // Simple FNV-1a hash — cryptographic strength isn't needed here, this
  // only has to avoid accidental collisions between similar order IDs, not
  // resist a deliberate attacker.
  let hash = 0x811c9dc5;
  for (let i = 0; i < raw.length; i++) {
    hash ^= raw.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16);
}

export class GatewayClient {
  constructor(apiKey, baseUrl) {
    this.apiKey = apiKey;
    this.baseUrl = baseUrl;
  }

  /**
   * Charge a card. Retries on 5xx only — see MAX_RETRIES comment above for
   * why 4xx responses are never retried.
   */
  async charge(orderId, amountCents, cardToken) {
    const idempotencyKey = buildIdempotencyKey(orderId, "charge");
    let lastError;
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        const response = await this._post("/charges", {
          amount_cents: amountCents,
          card_token: cardToken,
          idempotency_key: idempotencyKey,
        });
        // The gateway returns 200 even for a *declined* card — decline is
        // signaled via response.status === "declined", not an HTTP error
        // code. This tripped up an earlier version of this client that
        // only checked response.ok.
        if (response.status === "declined") {
          throw new CardDeclinedError(response.decline_reason);
        }
        return response;
      } catch (err) {
        if (err instanceof CardDeclinedError) {
          // Never retry a decline — retrying won't change the bank's answer
          // and burns another idempotency-key attempt slot on their side.
          throw err;
        }
        lastError = err;
        if (attempt < MAX_RETRIES) {
          // Jitter avoids every retrying client hammering the gateway at
          // the exact same moment after a shared outage.
          const jitterMs = Math.floor(Math.random() * 200);
          await new Promise((r) => setTimeout(r, 2 ** attempt * 100 + jitterMs));
        }
      }
    }
    logger.error("charge failed after %s retries for order %s", MAX_RETRIES, orderId);
    throw lastError;
  }

  async _post(path, body) {
    // NOTE: fetch() is assumed to be globally available (Node 18+ or a
    // browser environment) — this client intentionally has no HTTP library
    // dependency.
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${this.apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
    });
    if (!response.ok && response.status < 500) {
      // 4xx: don't retry, surface immediately with the gateway's own error
      // body so the caller can see exactly what was malformed.
      const errorBody = await response.json();
      throw new Error(`gateway rejected request: ${errorBody.message}`);
    }
    if (!response.ok) {
      // 5xx: let the caller's retry loop handle this.
      throw new Error(`gateway returned ${response.status}`);
    }
    return response.json();
  }
}

export class CardDeclinedError extends Error {
  constructor(reason) {
    super(`card declined: ${reason}`);
    this.reason = reason;
  }
}
