/**
 * Local-auth helper for Playwright e2e against the laptop-offline
 * profile (``SHIP_USE_MEMORY_ADAPTERS=true``, ``SHIP_AUTH_MODE=local``).
 *
 * The deployed-Auth0 suite uses a saved ``storageState`` JSON minted
 * once via ``playwright codegen`` + Auth0 login. Local mode has no
 * Auth0 round-trip — the backend mints a session JWT directly from
 * the local-login form. We mirror that flow here so a fresh laptop
 * dev can run the e2e suite without saving anything:
 *
 *   1. POST to ``${E2E_SHIP_API_BASE}/v1/auth/local/login`` with the
 *      seed-user credentials (``dev@ship.dev`` / ``dev``).
 *   2. Convert the returned access token into the ``ship_session``
 *      httpOnly cookie the Console reads server-side.
 *   3. Hand back a storageState shape Playwright can ``use``.
 *
 * Gated: when ``E2E_LOCAL_AUTH=true`` is set we run; otherwise the
 * caller falls back to whatever ``E2E_STORAGE_STATE`` points at.
 */

import type { APIRequestContext } from "@playwright/test";


export interface LocalAuthCredentials {
  email: string;
  password: string;
}


export interface LocalStorageState {
  cookies: Array<{
    name: string;
    value: string;
    domain: string;
    path: string;
    expires?: number;
    httpOnly: boolean;
    secure: boolean;
    sameSite?: "Strict" | "Lax" | "None";
  }>;
  origins: Array<{ origin: string; localStorage: unknown[] }>;
}


export function localAuthEnabled(): boolean {
  return process.env.E2E_LOCAL_AUTH === "true";
}


export function localCredentials(): LocalAuthCredentials {
  return {
    email: process.env.E2E_LOCAL_USER_EMAIL ?? "dev@ship.dev",
    password: process.env.E2E_LOCAL_USER_PASSWORD ?? "dev",
  };
}


/**
 * Drive the local-auth login flow + return a storageState ready to
 * feed Playwright's ``browserContext.use`` parameter. The cookie
 * domain is derived from ``E2E_CONSOLE_BASE_URL`` so the saved
 * state works against any localhost port the operator picks.
 */
export async function buildLocalStorageState(
  request: APIRequestContext,
): Promise<LocalStorageState> {
  const apiBase = (process.env.E2E_SHIP_API_BASE ?? "").replace(/\/+$/, "");
  const consoleBase = (
    process.env.E2E_CONSOLE_BASE_URL ?? "http://localhost:3001"
  ).replace(/\/+$/, "");
  if (!apiBase) {
    throw new Error(
      "E2E_SHIP_API_BASE is required for local-auth (laptop profile)",
    );
  }
  const creds = localCredentials();
  const res = await request.post(`${apiBase}/v1/auth/local/login`, {
    headers: { "Content-Type": "application/json" },
    data: JSON.stringify(creds),
  });
  if (!res.ok()) {
    throw new Error(
      `local-auth login failed: ${res.status()} ${await res.text()}`,
    );
  }
  const body = (await res.json()) as {
    access_token: string;
    expires_at: string;
  };
  const url = new URL(consoleBase);
  const expires =
    Math.floor(new Date(body.expires_at).getTime() / 1000) || undefined;
  return {
    cookies: [
      {
        name: "ship_session",
        value: body.access_token,
        domain: url.hostname,
        path: "/",
        expires,
        httpOnly: true,
        secure: url.protocol === "https:",
        sameSite: "Lax",
      },
    ],
    origins: [],
  };
}
