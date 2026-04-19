/**
 * Native form POST endpoint for sign-in.
 *
 * Why a route handler instead of a Server Action?
 * Cookies set inside an action that's invoked via `useActionState` get
 * delivered as `Set-Cookie` on an RSC fetch response, and Chromium silently
 * drops them in some Next 15 standalone configurations. A plain
 * `<form action="/api/auth/login" method="POST">` triggers a regular
 * top-level navigation, where the browser commits Set-Cookie before
 * following the 303 redirect — exactly what we need.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  login as apiLogin,
} from "@/lib/api/client";
import { setSessionCookie } from "@/lib/api/session";
import { resolveOrigin } from "@/lib/api/origin";

const COOKIE_NAME = "ship_session";

export async function POST(request: Request) {
  const form = await request.formData();
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  const next = String(form.get("next") ?? "/") || "/";
  const origin = resolveOrigin(request);

  if (!email || !password) {
    return errorRedirect(origin, "missing", email);
  }
  if (!isApiConfigured()) {
    return errorRedirect(origin, "no-backend", email);
  }

  try {
    const session = await apiLogin(email, password);
    await setSessionCookie(session.access_token, new Date(session.expires_at));
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return errorRedirect(origin, "invalid", email);
    }
    if (err instanceof ApiUnavailableError) {
      return errorRedirect(origin, "unavailable", email);
    }
    return errorRedirect(origin, "unknown", email);
  }
  // 303 turns the POST into a GET on the destination, which is what the spec
  // wants for "form-based authentication completed successfully".
  return NextResponse.redirect(new URL(next, origin), 303);
}

function errorRedirect(origin: string, code: string, email: string) {
  const url = new URL("/login", origin);
  url.searchParams.set("error", code);
  if (email) url.searchParams.set("email", email);
  const res = NextResponse.redirect(url, 303);
  // Best-effort: clear any stale session so the redirect doesn't bounce off
  // a half-set cookie from a previous attempt.
  res.cookies.delete(COOKIE_NAME);
  return res;
}
