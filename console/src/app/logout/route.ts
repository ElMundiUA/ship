/**
 * Logout endpoint.
 *
 * POST-only on purpose: a GET handler would be silently prefetched by
 * Next.js whenever a `<Link href="/logout">` rendered on the page, which
 * would expire the session cookie behind the user's back the moment they
 * loaded any chrome that includes it.
 */

import { NextResponse } from "next/server";

import { isAuth0Mode } from "@/lib/auth0";
import { clearSessionCookie } from "@/lib/api/session";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  if (isAuth0Mode) {
    // Hand off to the SDK-mounted route — it clears the encrypted session
    // cookie *and* redirects through Auth0's RP-initiated logout endpoint
    // so the IdP-side session goes away too.
    //
    // ``returnTo=/login`` lands the user on the sign-in page after Auth0
    // confirms the logout instead of the bare app root. The SDK joins this
    // with ``appBaseUrl`` and forwards as ``post_logout_redirect_uri`` —
    // the absolute URL must be in the application's "Allowed Logout URLs"
    // in the Auth0 dashboard, otherwise Auth0 returns 400 and the user
    // stays signed in.
    const url = new URL("/auth/logout", resolveOrigin(request));
    url.searchParams.set("returnTo", "/login");
    return NextResponse.redirect(url, 303);
  }
  await clearSessionCookie();
  return NextResponse.redirect(new URL("/login", resolveOrigin(request)), 303);
}
