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
function resolveAppBaseUrl(): string {
  // ``APP_BASE_URL`` is the canonical Auth0-SDK input. ``AUTH0_BASE_URL``
  // is kept as a legacy alias from the v3 SDK era. ``SHIP_CONSOLE_URL``
  // is the same value backend-side; we treat it as a last-ditch
  // alignment fallback so prod can't silently land on localhost just
  // because the env was renamed under our feet.
  const candidates = [
    process.env.APP_BASE_URL,
    process.env.AUTH0_BASE_URL,
    process.env.SHIP_CONSOLE_URL,
  ].map((v) => (v ?? "").trim());
  const picked = candidates.find((v) => v.length > 0);
  // In local dev (SHIP_AUTH_MODE=local) this function isn't called.
  // In auth0 mode we MUST have a real origin — silently falling back
  // to ``http://localhost:3001`` is what bounced prod users onto the
  // "site can't be reached" tab after Auth0 finished the login round
  // trip. Throw at construct time instead so the pod fails its
  // readiness probe loudly.
  if (!picked) {
    throw new Error(
      "auth0 mode requires APP_BASE_URL (or AUTH0_BASE_URL / SHIP_CONSOLE_URL); " +
        "none set. Fix your env and redeploy.",
    );
  }
  if (/localhost|127\.0\.0\.1|0\.0\.0\.0/.test(picked)) {
    throw new Error(
      `auth0 mode refusing to launch with localhost-shaped ` +
        `appBaseUrl=${picked}. Set APP_BASE_URL to your real public ` +
        "origin (e.g. https://app.ship.elmundi.com) and redeploy. " +
        "Mirrors the backend-side guard in " +
        "apps/backend/app/core/config.py:_no_localhost_urls_in_cloud.",
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
