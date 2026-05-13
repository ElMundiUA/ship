/**
 * Complete profile endpoint for Auth0 users with missing email claim.
 *
 * Called when a user logs in via an SSO connection that doesn't provide an
 * email claim (e.g. enterprise SAML). The /complete-profile page posts the
 * email here, which proxies to the backend to persist it.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const form = await request.formData();
  const email = String(form.get("email") ?? "").trim();
  const next = String(form.get("next") ?? "/") || "/";
  const origin = resolveOrigin(request);

  if (!email) {
    return errorRedirect(origin, "missing");
  }
  if (!isApiConfigured()) {
    return errorRedirect(origin, "no-backend");
  }

  const token = await getSessionToken();
  if (!token) {
    return errorRedirect(origin, "no-session");
  }

  try {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
    const response = await fetch(`${apiUrl}/v1/auth/complete-profile`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ email }),
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      if (response.status === 409) {
        return errorRedirect(origin, "email-taken");
      }
      if (response.status === 400) {
        return errorRedirect(origin, "invalid-request");
      }
      return errorRedirect(origin, "unknown");
    }

    // Success — redirect to the next page
    return NextResponse.redirect(new URL(next, origin), 303);
  } catch (err) {
    return errorRedirect(origin, "unknown");
  }
}

function errorRedirect(origin: string, code: string) {
  const url = new URL("/complete-profile", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
