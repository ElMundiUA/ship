/**
 * POST /api/inbox-groups/members — add or remove a group member.
 *
 * Single endpoint for both ops to keep the form footprint small:
 * `op=add` calls POST .../groups/{id}/members; `op=remove` calls
 * DELETE .../groups/{id}/members/{user_id}.
 *
 * Form fields:
 *   ws          workspace id
 *   group_id    group uuid
 *   op          'add' | 'remove'
 *   user_id     uuid of the workspace member to add/remove
 *   on_call     '1' to flag the new member as on-call (add only)
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  addInboxGroupMember,
  isApiConfigured,
  removeInboxGroupMember,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const groupId = (form.get("group_id") ?? "").toString();
  const op = (form.get("op") ?? "").toString();
  const userId = (form.get("user_id") ?? "").toString();
  const onCall = (form.get("on_call") ?? "").toString() === "1";

  if (!wsId || !groupId || !userId || (op !== "add" && op !== "remove"))
    return back(origin, groupId, "bad_input");
  if (!isApiConfigured()) return back(origin, groupId, "api_unavailable");

  try {
    if (op === "add") {
      await addInboxGroupMember(wsId, groupId, { user_id: userId, on_call: onCall });
    } else {
      await removeInboxGroupMember(wsId, groupId, userId);
    }
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, groupId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, groupId, "forbidden");
      if (err.status === 404) return back(origin, groupId, "not_found");
      if (err.status === 409) return back(origin, groupId, "duplicate");
      if (err.status === 422) return back(origin, groupId, "not_workspace_member");
      return back(origin, groupId, `http_${err.status}`);
    }
    return back(origin, groupId, "unknown");
  }

  const url = new URL("/settings/groups", origin);
  url.searchParams.set("group", groupId);
  return NextResponse.redirect(url, 303);
}

function back(origin: string, groupId: string, code: string) {
  const url = new URL("/settings/groups", origin);
  if (groupId) url.searchParams.set("group", groupId);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
