/**
 * POST /api/integrations/native-gitlab — connect GitLab via PAT.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  connectGitLabPat,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const host = (form.get("host") ?? "").toString().trim();
  const group = (form.get("group") ?? "").toString().trim();
  const pat = (form.get("pat") ?? "").toString();
  const next = (form.get("next") ?? "/integrations").toString();

  if (!wsId || !host || !pat) {
    return back(origin, next, "bad_input");
  }
  if (!isApiConfigured()) return back(origin, next, "api_unavailable");

  try {
    await connectGitLabPat(wsId, {
      host,
      group: group || null,
      pat,
      scopes: ["read_api", "read_repository"],
    });
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return back(origin, next, "api_unavailable");
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      return back(origin, next, `http_${err.status}`);
    }
    return back(origin, next, "unknown");
  }

  const url = new URL(next.startsWith("/") ? next : "/integrations", origin);
  url.searchParams.set("gitlab", "connected");
  return NextResponse.redirect(url, 303);
}

function back(origin: string, next: string, code: string) {
  const url = new URL(next.startsWith("/") ? next : "/integrations", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
