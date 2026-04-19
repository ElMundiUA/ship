/**
 * Session cookie helpers.
 *
 * The console stores the bearer token from `/v1/auth/local/login` in an
 * httpOnly cookie named `ship_session`. Browser code never sees it; every
 * server component / server action reads it via these helpers.
 */

import "server-only";

import { cookies } from "next/headers";

const COOKIE_NAME = "ship_session";

export async function getSessionToken(): Promise<string | null> {
  const jar = await cookies();
  const c = jar.get(COOKIE_NAME);
  return c?.value ?? null;
}

export async function setSessionCookie(token: string, expiresAt: Date): Promise<void> {
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
  const jar = await cookies();
  jar.delete(COOKIE_NAME);
}
