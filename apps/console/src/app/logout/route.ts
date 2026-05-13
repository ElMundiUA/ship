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
    // The SDK forwards ``returnTo`` as ``post_logout_redirect_uri`` *as
    // posted* — it does not prefix the value with ``appBaseUrl``. So we
    // pass the absolute URL here (origin from the request); Auth0
    // validates that exact string against the application's
    // "Allowed Logout URLs" in the dashboard. A relative ``/login``
    // alone would be sent verbatim and 400.
    const origin = resolveOrigin(request);
    const url = new URL("/auth/logout", origin);
    url.searchParams.set("returnTo", `${origin}/login`);
    return NextResponse.redirect(url, 303);
  }
  await clearSessionCookie();
  return NextResponse.redirect(new URL("/login", resolveOrigin(request)), 303);
}
