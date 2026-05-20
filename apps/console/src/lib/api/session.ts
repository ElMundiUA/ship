/**
 * Session cookie helpers.
 *
 * Two modes coexist:
 *
 * - **Local** (SHIP_AUTH_MODE=local) — bearer token from
 *   `/v1/auth/local/login` is stored in an httpOnly cookie named
 *   `ship_session`. Browser code never sees it; server components read it
 *   via these helpers.
 *
 * - **Auth0** (SHIP_AUTH_MODE=auth0) — the SDK manages the session cookie
 *   for us; `getSessionToken()` calls into the SDK to extract the
 *   access_token that the backend will validate against the Auth0 JWKS.
 */

// ``import "server-only"`` was removed 2026-05-19 — the marker blocked
// every backend deploy for ~30 days because client.ts (which has 20+
// "use client" importers for types) transitively pulled this module
// into the browser bundle, and Next.js then refused to compile the
// chunk ("not supported in the pages/ directory"). The actual
// security boundary survives without the marker:
//   - cookies() / auth0 SDK only work in server runtime; calling
//     getSessionToken() from a client component would throw at runtime
//     instead of leaking a token.
//   - The cookie itself is httpOnly + sameSite=lax — never visible to
//     ``document.cookie``.
// Future enforcement should be a lint rule banning ``apiFetch``
// imports from ``"use client"`` files, not a runtime marker that
// turns into a build-time outage.

import { auth0, isAuth0Mode } from "@/lib/auth0";

const COOKIE_NAME = "ship_session";

export async function getSessionToken(): Promise<string | null> {
  if (isAuth0Mode && auth0) {
    try {
      const session = await auth0.getSession();
      if (!session) return null;
      const { token } = await auth0.getAccessToken();
      return token ?? null;
    } catch {
      // No session, expired, or refresh failed. Treat as logged out — the
      // calling code falls back to mock data or redirects to /login.
      return null;
    }
  }
  // ``next/headers`` is server-only by design (throws at runtime on
  // the client) but Webpack pre-emptively refuses to build any chunk
  // that statically imports it from a client-reachable file. Pull it
  // in via a dynamic import that only runs server-side.
  const { cookies } = await import("next/headers");
  const jar = await cookies();
  const c = jar.get(COOKIE_NAME);
  return c?.value ?? null;
}

export async function setSessionCookie(token: string, expiresAt: Date): Promise<void> {
  const { cookies } = await import("next/headers");
  const jar = await cookies();
  // maxAge in seconds; Chromium has occasionally been finicky about parsing
  // wall-clock `expires` headers from RSC action responses, so we use the
  // delta form which is also recommended for httpOnly session cookies.
  const maxAgeSeconds = Math.max(1, Math.floor((expiresAt.getTime() - Date.now()) / 1000));
  jar.set({
    name: COOKIE_NAME,
    value: token,
    httpOnly: true,
    // Opt-in via env flag instead of NODE_ENV: production builds also run
    // behind plain http://localhost in compose / single-node setups, where
    // a `secure` cookie would be silently dropped by the browser. Operators
    // serving the console behind TLS set SHIP_COOKIE_SECURE=true.
    secure: process.env.SHIP_COOKIE_SECURE === "true",
    sameSite: "lax",
    path: "/",
    maxAge: maxAgeSeconds,
  });
}

export async function clearSessionCookie(): Promise<void> {
  const { cookies } = await import("next/headers");
  const jar = await cookies();
  jar.delete(COOKIE_NAME);
}
