/**
 * POST /api/tokens/revoke — soft-delete a PAT.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  revokeToken,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const tokenId = (form.get("token") ?? "").toString();
  if (!tokenId) return back(origin, "bad_input");
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await revokeToken(tokenId);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 404) return back(origin, "not_found");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  const url = new URL("/settings", origin);
  url.searchParams.set("tab", "tokens");
  return NextResponse.redirect(url, 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings", origin);
  url.searchParams.set("tab", "tokens");
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
