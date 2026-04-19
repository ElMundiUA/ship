/**
 * Logout endpoint.
 *
 * POST-only on purpose: a GET handler would be silently prefetched by
 * Next.js whenever a `<Link href="/logout">` rendered on the page, which
 * would expire the session cookie behind the user's back the moment they
 * loaded any chrome that includes it.
 */

import { NextResponse } from "next/server";

import { clearSessionCookie } from "@/lib/api/session";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  await clearSessionCookie();
  return NextResponse.redirect(new URL("/login", resolveOrigin(request)), 303);
}
