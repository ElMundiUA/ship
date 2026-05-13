/**
 * POST /api/members/specialists — update which specialist Inbox lanes a
 * member can answer (BA, QA, …, or * for all).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  patchMember,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";
import { workspaceMembersSettingsUrl } from "@/lib/members-settings-url";

const SLUGS = ["ba", "qa", "eng", "sec", "pm", "dev"] as const;

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const memberId = (form.get("member") ?? "").toString();
  const isOwnerRow = (form.get("is_owner") ?? "").toString() === "1";
  if (!wsId || !memberId) return back(origin, wsId, "bad_input");
  if (!isApiConfigured()) return back(origin, wsId, "api_unavailable");

  const allOn =
    (form.get("all_specialist") ?? "").toString() === "1" ||
    (form.get("all_specialist") ?? "").toString() === "on";

  let slugs: string[] = [];
  if (isOwnerRow && allOn) {
    slugs = ["*"];
  } else {
    for (const id of SLUGS) {
      const v = (form.get(`sp_${id}`) ?? "").toString();
      if (v === "1" || v === "on") slugs.push(id);
    }
  }

  try {
    await patchMember(wsId, memberId, { answer_specialist_slugs: slugs });
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      if (err.status === 403) return back(origin, wsId, "forbidden");
      if (err.status === 404) return back(origin, wsId, "not_found");
      if (err.status === 422) return back(origin, wsId, "bad_input");
      return back(origin, wsId, `http_${err.status}`);
    }
    return back(origin, wsId, "unknown");
  }

  return NextResponse.redirect(workspaceMembersSettingsUrl(origin, wsId), 303);
}

function back(origin: string, workspaceId: string, code: string) {
  const url = workspaceMembersSettingsUrl(origin, workspaceId, { error: code });
  return NextResponse.redirect(url, 303);
}
