/** Native form POST endpoint for signup. See /api/auth/login for the rationale. */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  signup as apiSignup,
} from "@/lib/api/client";
import { setSessionCookie } from "@/lib/api/session";
import { resolveOrigin } from "@/lib/api/origin";

const COOKIE_NAME = "ship_session";

export async function POST(request: Request) {
  const form = await request.formData();
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  const displayName =
    String(form.get("display_name") ?? "").trim() || undefined;
  const next = String(form.get("next") ?? "/") || "/";
  const origin = resolveOrigin(request);

  if (!email || !password) {
    return errorRedirect(origin, "missing", email);
  }
  if (password.length < 8) {
    return errorRedirect(origin, "weak-password", email);
  }
  if (!isApiConfigured()) {
    return errorRedirect(origin, "no-backend", email);
  }

  try {
    const session = await apiSignup(email, password, displayName);
    await setSessionCookie(session.access_token, new Date(session.expires_at));
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 409) {
      return errorRedirect(origin, "exists", email);
    }
    if (err instanceof ApiUnavailableError) {
      return errorRedirect(origin, "unavailable", email);
    }
    return errorRedirect(origin, "unknown", email);
  }
  return NextResponse.redirect(new URL(next, origin), 303);
}

function errorRedirect(origin: string, code: string, email: string) {
  const url = new URL("/login", origin);
  url.searchParams.set("mode", "signup");
  url.searchParams.set("error", code);
  if (email) url.searchParams.set("email", email);
  const res = NextResponse.redirect(url, 303);
  res.cookies.delete(COOKIE_NAME);
  return res;
}
