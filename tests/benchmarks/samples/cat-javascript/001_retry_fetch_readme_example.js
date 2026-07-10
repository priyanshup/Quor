/**
 * retry-fetch — a tiny wrapper around fetch() that retries on network
 * failure with exponential backoff.
 */

const DEFAULT_MAX_ATTEMPTS = 3;
const DEFAULT_BASE_DELAY_MS = 200;

/**
 * Fetch a URL, retrying up to `maxAttempts` times on failure.
 */
export async function retryFetch(url, options = {}, maxAttempts = DEFAULT_MAX_ATTEMPTS) {
  let lastError;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      return await fetch(url, options);
    } catch (err) {
      lastError = err;
      await sleep(DEFAULT_BASE_DELAY_MS * 2 ** attempt);
    }
  }
  throw lastError;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
