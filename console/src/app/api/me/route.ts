/**
 * GET /api/me — proxy to backend ``/v1/auth/me`` so client components
 * (AppShell sidebar, mostly) can read the signed-in user without
 * every page-level server component having to load + forward it.
 *
 * The session cookie stays httpOnly; the browser only sees the
 * de-anonymised user shape in the response.
 */

import { NextResponse } from "next/server";

import { getSessionToken } from "@/lib/api/session";

export const runtime = "nodejs";

export async function GET(): Promise<Response> {
  const base = process.env.SHIP_API_URL?.trim().replace(/\/+$/, "") ?? "";
  if (!base) {
    return NextResponse.json(
      { error: "backend_not_configured" },
      { status: 503 },
    );
  }
  const token = await getSessionToken();
  if (!token) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const upstream = await fetch(`${base}/v1/auth/me`, {
    headers: {
      authorization: `Bearer ${token}`,
      accept: "application/json",
    },
    cache: "no-store",
  });

  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: {
      "content-type":
        upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
