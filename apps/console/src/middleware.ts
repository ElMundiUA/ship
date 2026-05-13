/**
 * Next.js middleware — runs on every request before route handlers.
 *
 * In auth0 mode, delegate to the Auth0 SDK middleware so the auto-mounted
 * `/auth/*` routes (login, callback, logout, profile, access-token) work
 * and the session cookie is rotated on each request.
 *
 * In local mode this file is essentially a no-op so the legacy email +
 * password flow continues to work without any session-shape change.
 */

import { NextResponse, type NextRequest } from "next/server";

import { auth0, isAuth0Mode } from "@/lib/auth0";
import {
  isLikelyWorkspaceId,
  SHIP_ACTIVE_WORKSPACE_COOKIE,
} from "@/lib/workspace-scope";

function applyWorkspaceCookie(
  request: NextRequest,
  response: NextResponse,
): NextResponse {
  const ws = request.nextUrl.searchParams.get("ws");
  if (ws && isLikelyWorkspaceId(ws)) {
    response.cookies.set(SHIP_ACTIVE_WORKSPACE_COOKIE, ws, {
      path: "/",
      maxAge: 60 * 60 * 24 * 400,
      sameSite: "lax",
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
    });
  }
  return response;
}

export async function middleware(request: NextRequest) {
  let response: NextResponse;
  if (isAuth0Mode && auth0) {
    response = await auth0.middleware(request);
  } else {
    response = NextResponse.next();
  }
  return applyWorkspaceCookie(request, response);
}

export const config = {
  // Match every path except static assets and image optimisation. Without
  // this, Auth0's middleware burns rotation cycles on every favicon hit.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt).*)",
  ],
};
