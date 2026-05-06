import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  wizardSeed,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  const form = await request.formData();
  const workspaceId = stringField(form, "workspaceId");
  const repoId = stringField(form, "repoId");
  // ``include_fsm`` is sent by the table row based on the repo's
  // current ``process:`` config state. ``"false"`` for FSM-ready
  // repos so a bundle bump doesn't silently rewrite the operator's
  // tailored process block; ``"true"`` for repos without a process
  // block yet (full seed mirrors first-time-setup wizard behaviour).
  // Defaults to ``true`` if the field is absent (legacy callers).
  const includeFsm = stringField(form, "include_fsm") !== "false";

  if (!workspaceId || !repoId) {
    return redirectToSettings(request, "bad_input");
  }
  if (!isApiConfigured()) {
    return redirectToSettings(request, "api_unavailable");
  }

  const token = (await getSessionToken()) ?? undefined;
  try {
    const result = await wizardSeed(
      workspaceId,
      repoId,
      { include_fsm: includeFsm, rotate_run_token: false },
      token,
    );
    return NextResponse.redirect(result.pr_url, 303);
  } catch (err) {
    return redirectToSettings(request, errorCode(err));
  }
}

function stringField(form: FormData, key: string): string | null {
  const value = form.get(key);
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function redirectToSettings(request: Request, error: string) {
  const url = new URL("/settings", request.url);
  url.searchParams.set("tab", "repositories");
  url.searchParams.set("error", error);
  return NextResponse.redirect(url, 303);
}

function errorCode(err: unknown): string {
  if (err instanceof ApiUnavailableError) return "api_unavailable";
  if (err instanceof ApiHttpError) {
    if (err.status === 401) return "session_expired";
    if (err.status === 403) return "forbidden";
    if (err.status === 404) return "not_found";
    if (err.status === 412) {
      const detail = err.detail as { code?: string } | null;
      return detail?.code ?? "precondition_failed";
    }
    if (err.status === 422) return "bad_body";
    if (err.status === 502) return "github_api_error";
    return `http_${err.status}`;
  }
  return "unknown";
}
