/**
 * Auth0 SDK initialization for the console (server-side only).
 *
 * Driven by SHIP_AUTH_MODE:
 *   - "auth0"  → real Auth0 OIDC flow via @auth0/nextjs-auth0
 *   - "local"  → no-op shim; the legacy email/password form (and its
 *                ship_session cookie) keeps working for laptop dev.
 *
 * v4 of the SDK auto-mounts the auth routes (/auth/login, /auth/callback,
 * /auth/logout, /auth/profile, /auth/access-token) from middleware.ts; we
 * never write a [auth0] route handler ourselves.
 *
 * Environment expectations (set by scripts/bootstrap.sh --auth0):
 *   AUTH0_DOMAIN              your-tenant.eu.auth0.com
 *   AUTH0_CLIENT_ID           console application client id
 *   AUTH0_CLIENT_SECRET       console application secret
 *   AUTH0_AUDIENCE            API identifier (must match backend AUTH0_AUDIENCE)
 *   AUTH0_SESSION_SECRET      32-byte hex; encrypts the session cookie
 *   APP_BASE_URL              public URL of the console (e.g. http://localhost:3001)
 */

import "server-only";

import { Auth0Client } from "@auth0/nextjs-auth0/server";

export const AUTH_MODE = (process.env.SHIP_AUTH_MODE ?? "local").toLowerCase();
export const isAuth0Mode = AUTH_MODE === "auth0";

/**
 * Construct the SDK client only in auth0 mode. Constructing it eagerly when
 * SHIP_AUTH_MODE=local would crash dev servers that haven't filled the
 * AUTH0_* placeholders, since the v4 client throws on missing required
 * config at construct time.
 */
/**
 * Resolve the appBaseUrl Auth0 hands to clients as the OAuth redirect
 * origin. Walks ``APP_BASE_URL → AUTH0_BASE_URL → SHIP_CONSOLE_URL``
 * before falling back to localhost.
 *
 * **Diagnostic only, never throws.** A throw at construct time would
 * brick prod every time the secret drifts — and we already lost a
 * console rollout to that pattern once (auth0-guard v1, reverted via
 * commit ``bb6e94f``). The right place to refuse a localhost-shaped
 * origin in cloud mode is at boot-time *runtime* (a probe endpoint
 * that surfaces the misconfiguration) or at the backend, where
 * ``apps/backend/app/core/config.py:_no_localhost_urls_in_cloud``
 * already fails fast.
 */
function resolveAppBaseUrl(): string {
  const picked = [
    process.env.APP_BASE_URL,
    process.env.AUTH0_BASE_URL,
    process.env.SHIP_CONSOLE_URL,
  ]
    .map((v) => (v ?? "").trim())
    .find((v) => v.length > 0);
  if (!picked) {
    console.error(
      "[auth0] APP_BASE_URL / AUTH0_BASE_URL / SHIP_CONSOLE_URL all unset; " +
        "falling back to http://localhost:3001. Auth0 redirects will be " +
        "broken in cloud mode — set one of these to your public origin.",
    );
    return "http://localhost:3001";
  }
  if (/localhost|127\.0\.0\.1|0\.0\.0\.0/.test(picked)) {
    console.error(
      `[auth0] appBaseUrl resolved to localhost-shaped value (${picked}). ` +
        "Auth0 will redirect users to that origin after login — works on " +
        "laptop dev only. In cloud mode set APP_BASE_URL to your public " +
        "origin (e.g. https://app.ship.elmundi.com) and roll out the " +
        "console deployment.",
    );
  }
  return picked;
}

export const auth0 = isAuth0Mode
  ? new Auth0Client({
      domain: process.env.AUTH0_DOMAIN ?? "",
      clientId: process.env.AUTH0_CLIENT_ID ?? "",
      clientSecret: process.env.AUTH0_CLIENT_SECRET ?? "",
      appBaseUrl: resolveAppBaseUrl(),
      secret: process.env.AUTH0_SESSION_SECRET ?? "",
      authorizationParameters: {
        audience: process.env.AUTH0_AUDIENCE,
        // Request a long-lived access token so the operator console can
        // stay logged in for the duration of a working day without
        // triggering a silent refresh round-trip on every render.
        scope: "openid profile email offline_access",
      },
    })
  : null;
