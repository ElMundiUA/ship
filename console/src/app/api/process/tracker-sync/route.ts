import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  syncTrackerProjection,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";
import { getSessionToken } from "@/lib/api/session";

/**
 * Form-action proxy for the tracker projection sync flow.
 *
 * Posts ``workspaceId`` + ``processId`` + ``repoId`` to the backend's
 * ``POST /v1/.../tracker-sync`` route, which probes the bound tracker,
 * runs the deterministic + LLM resolver with validation+retry, and
 * opens a single-file PR rewriting ``process.tracker_mapping`` in
 * ``.ship/config.yml``. On success we 303-redirect to the PR; on
 * failure we bounce the operator back to /process with a reason code
 * the editor surfaces in its top banner.
 *
 * Mirrors the shape of ``/api/process/config-propose`` so the editor's
 * existing form-redirect pattern stays consistent — no client-side
 * state machine needed for what is fundamentally a "click button,
 * land on PR" UX.
 */
export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const workspaceId = formValue(form, "workspaceId");
  const processId = formValue(form, "processId");
  const repoId = formValue(form, "repoId");

  if (!workspaceId || !processId || !repoId) {
    return back(origin, repoId, "tracker_sync_bad_request");
  }
  if (!isApiConfigured()) {
    return back(origin, repoId, "api_unavailable");
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const result = await syncTrackerProjection(
      workspaceId,
      processId,
      {
        repo_id: repoId,
        change_summary:
          formValue(form, "changeSummary") ||
          "Align tracker_mapping with workspace's actual tracker workflow.",
      },
      token,
    );
    return NextResponse.redirect(result.pr_url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return back(origin, repoId, "api_unavailable");
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      // Surface the most useful backend error code to the editor banner.
      // The backend returns ``code`` inside the detail payload for the
      // common 412/422 paths (tracker_not_bound, github_app_missing,
      // process_block_missing, …); fall back to the HTTP status when it
      // doesn't.
      const code = errorCodeFromDetail(err) ?? `http_${err.status}`;
      return back(origin, repoId, `tracker_sync_${code}`);
    }
    return back(origin, repoId, "tracker_sync_unknown");
  }
}

function back(origin: string, repoId: string | null, reason: string) {
  const url = new URL("/process", origin);
  if (repoId) url.searchParams.set("repo", repoId);
  url.searchParams.set("reason", reason);
  return NextResponse.redirect(url, 303);
}

function formValue(form: FormData, key: string): string {
  return (form.get(key) ?? "").toString();
}

function errorCodeFromDetail(err: ApiHttpError): string | null {
  const detail = err.detail as unknown;
  if (
    detail &&
    typeof detail === "object" &&
    !Array.isArray(detail) &&
    "code" in detail &&
    typeof (detail as { code: unknown }).code === "string"
  ) {
    return (detail as { code: string }).code;
  }
  return null;
}
