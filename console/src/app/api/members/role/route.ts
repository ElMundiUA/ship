/**
 * POST /api/members/role — change a member's role.
 *
 * Each role <select> is wrapped in a tiny no-JS form posting here. The
 * select's ``onchange`` is a plain submit on the surrounding form (no client
 * JS); the row's hidden inputs identify the workspace + member.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  updateMemberRole,
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
  const memberId = (form.get("member") ?? "").toString();
  const roleRaw = (form.get("role") ?? "").toString();
  if (!wsId || !memberId || !VALID_ROLES.includes(roleRaw as ApiMemberRole)) {
    return back(origin, "bad_input");
  }
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await updateMemberRole(wsId, memberId, roleRaw as ApiMemberRole);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 404) return back(origin, "not_found");
      if (err.status === 409) return back(origin, "last_owner");
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
