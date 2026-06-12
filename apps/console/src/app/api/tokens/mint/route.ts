/**
 * POST /api/tokens/mint — mint a new PAT for the current user.
 *
 * The freshly minted secret is shown exactly once. We stash it in a one-shot
 * cookie so the redirected /settings page can render it inline and then
 * immediately clear the cookie. (Better than putting the secret in the URL
 * — that would leak into browser history and server access logs.)
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  mintToken,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const FRESH_SECRET_COOKIE = "ship_token_just_minted";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  // JSON mode (ELS-288): the Connect-your-agent card fetches with
  // ``Accept: application/json`` and renders the one-time secret
  // inline instead of bouncing through the cookie + redirect dance.
  const wantsJson = (request.headers.get("accept") ?? "").includes(
    "application/json",
  );
  const fail = (code: string, status = 400) =>
    wantsJson
      ? NextResponse.json({ error: code }, { status })
      : back(origin, code);
  const form = await request.formData();
  const name = (form.get("name") ?? "").toString().trim();
  const wsId = (form.get("ws") ?? "").toString().trim();
  const ttlRaw = (form.get("ttl") ?? "").toString().trim();
  const ttlDays = ttlRaw === "" ? undefined : Number.parseInt(ttlRaw, 10);
  if (!name) return fail("bad_input");
  if (ttlDays !== undefined && (Number.isNaN(ttlDays) || ttlDays < 1)) {
    return fail("bad_ttl");
  }
  if (!isApiConfigured()) return fail("api_unavailable", 503);

  let secret: string;
  try {
    const minted = await mintToken({
      name,
      workspace_id: wsId || undefined,
      ttl_days: ttlDays,
    });
    secret = minted.secret;
  } catch (err) {
    if (err instanceof ApiUnavailableError) return fail("api_unavailable", 503);
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return wantsJson
          ? NextResponse.json({ error: "session_expired" }, { status: 401 })
          : NextResponse.redirect(
              new URL("/login?error=session_expired", origin),
              303,
            );
      }
      if (err.status === 403) return fail("forbidden", 403);
      return fail(`http_${err.status}`, 502);
    }
    return fail("unknown", 500);
  }

  if (wantsJson) {
    return NextResponse.json({ secret });
  }

  const url = new URL("/settings", origin);
  url.searchParams.set("tab", "tokens");
  url.searchParams.set("just_minted", "1");
  const res = NextResponse.redirect(url, 303);
  // ~5 minutes lives long enough to copy the secret but won't sit in cookies
  // forever; the page reads-then-clears it on render.
  res.cookies.set(FRESH_SECRET_COOKIE, secret, {
    httpOnly: true,
    sameSite: "lax",
    secure: origin.startsWith("https://"),
    path: "/",
    maxAge: 300,
  });
  return res;
}

function back(origin: string, code: string) {
  const url = new URL("/settings", origin);
  url.searchParams.set("tab", "tokens");
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
