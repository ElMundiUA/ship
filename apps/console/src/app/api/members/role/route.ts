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
import { workspaceMembersSettingsUrl } from "@/lib/members-settings-url";

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
    return back(origin, undefined, "bad_input");
  }
  if (!isApiConfigured()) return back(origin, wsId, "api_unavailable");

  try {
    await updateMemberRole(wsId, memberId, roleRaw as ApiMemberRole);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return back(origin, wsId, "forbidden");
      if (err.status === 404) return back(origin, wsId, "not_found");
      if (err.status === 409) return back(origin, wsId, "last_owner");
      return back(origin, wsId, `http_${err.status}`);
    }
    return back(origin, wsId, "unknown");
  }

  return NextResponse.redirect(workspaceMembersSettingsUrl(origin, wsId), 303);
}

function back(origin: string, workspaceId: string | undefined, code: string) {
  const url = workspaceMembersSettingsUrl(origin, workspaceId, { error: code });
  return NextResponse.redirect(url, 303);
}
