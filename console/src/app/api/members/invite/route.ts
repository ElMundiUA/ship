/**
 * POST /api/members/invite — pre-provision a workspace member.
 *
 * Mirrors the artifact-repo create handler: the member form posts here and
 * we forward to `POST /v1/workspaces/{id}/members`. Bounce back to /members
 * with a query-string error code on failure so the page can render a
 * human-readable banner.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  inviteMember,
  isApiConfigured,
} from "@/lib/api/client";
import type { ApiMemberRole } from "@/lib/api/types";
import { resolveOrigin } from "@/lib/api/origin";

const VALID_ROLES: readonly ApiMemberRole[] = [
  "owner",
  "admin",
  "maintainer",
  "member",
  "viewer",
];

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const email = (form.get("email") ?? "").toString().trim().toLowerCase();
  const roleRaw = (form.get("role") ?? "member").toString();
  const role = (VALID_ROLES.includes(roleRaw as ApiMemberRole)
    ? roleRaw
    : "member") as ApiMemberRole;
  if (!wsId || !email) return back(origin, "bad_input");
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await inviteMember(wsId, { email, role });
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 404) return back(origin, "not_found");
      if (err.status === 409) return back(origin, "duplicate");
      if (err.status === 422) return back(origin, "invalid_email");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  return NextResponse.redirect(new URL("/members", origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL("/members", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
