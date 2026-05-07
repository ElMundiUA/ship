/**
 * Shared transient-failure retry for shipctl HTTP calls.
 *
 * Wraps a ``fetch``-style call with three retry attempts on:
 *
 *   - ``fetch`` itself throwing (DNS / TCP / TLS — typical Bunny edge
 *     half-close).
 *   - HTTP ``502`` / ``503`` / ``504`` responses (origin-unreachable
 *     edge replies that almost always recover within 1-3 seconds).
 *
 * 4xx and other 5xx codes are passed through on the first attempt so
 * a real bug surfaces fast. Used by ``apiRequest`` in ``agent_api.mjs``
 * / ``commands/trigger.mjs`` / ``commands/knowledge.mjs`` and by the
 * one-shot ``fetch`` calls in ``commands/run.mjs`` / ``preflight.mjs``
 * which bypass the helper for response-shape reasons.
 */

const RETRY_DELAYS_MS = [500, 1500, 4500];
const TRANSIENT_STATUSES = new Set([502, 503, 504]);


/**
 * @param {() => Promise<Response>} doFetch
 * @param {{ description?: string, onWarn?: (msg: string) => void }} [opts]
 * @returns {Promise<Response>}
 */
export async function fetchWithRetry(doFetch, opts = {}) {
  const description = opts.description || "request";
  const warn =
    opts.onWarn || ((msg) => console.error(`warn: ${msg}`));
  let lastError = null;
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt += 1) {
    if (attempt > 0) {
      await new Promise((r) => setTimeout(r, RETRY_DELAYS_MS[attempt - 1]));
    }
    let res;
    try {
      res = await doFetch();
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      if (attempt < RETRY_DELAYS_MS.length) {
        warn(
          `network error on ${description} (attempt ${attempt + 1}/${RETRY_DELAYS_MS.length + 1}): ${lastError.message}`,
        );
        continue;
      }
      throw lastError;
    }
    if (
      TRANSIENT_STATUSES.has(res.status)
      && attempt < RETRY_DELAYS_MS.length
    ) {
      warn(
        `transient ${res.status} on ${description} (attempt ${attempt + 1}/${RETRY_DELAYS_MS.length + 1}); retrying`,
      );
      continue;
    }
    return res;
  }
  // Unreachable — the loop above always returns or throws.
  throw lastError ?? new Error(`${description}: exhausted retries`);
}


export { RETRY_DELAYS_MS, TRANSIENT_STATUSES };
