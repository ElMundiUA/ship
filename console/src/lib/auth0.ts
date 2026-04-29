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
export const auth0 = isAuth0Mode
  ? new Auth0Client({
      domain: process.env.AUTH0_DOMAIN ?? "",
      clientId: process.env.AUTH0_CLIENT_ID ?? "",
      clientSecret: process.env.AUTH0_CLIENT_SECRET ?? "",
      appBaseUrl: process.env.APP_BASE_URL ?? "http://localhost:3001",
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
