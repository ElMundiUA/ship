/**
 * Proxy for the Library editor Save flow.
 *
 * Accepts JSON from ``library-editor.tsx`` and forwards to the
 * backend ``POST /v1/workspaces/{ws}/repos/{repo}/config/propose``.
 * Lives as an app-router API route (rather than going direct from
 * the browser) so the session cookie is available and the backend
 * URL never leaks into client bundles.
 *
 * Error shaping mirrors the backend:
 * - ``409`` with ``code=sha_mismatch`` → editor surfaces the drift
 *   banner and disables Save until reload.
 * - ``422`` with a structured ``code`` → we pass it through so the
 *   client can tell "empty lanes" from "bad cron" without parsing a
 *   free-form message.
 * - ``502`` with ``code=propose_failed`` → upstream GitHub error;
 *   the client renders the message verbatim.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  proposeRepoConfig,
  type ApiLaneTriggerIn,
} from "@/lib/api/client";

type ProposeRequestBody = {
  workspaceId: string;
  repoId: string;
  base_sha: string | null;
  lanes: Record<string, ApiLaneTriggerIn>;
  change_summary?: string;
  preset?: string | null;
};

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  let body: ProposeRequestBody;
  try {
    body = (await request.json()) as ProposeRequestBody;
  } catch {
    return NextResponse.json(
      { error: "Invalid JSON body.", code: "bad_request" },
      { status: 400 },
    );
  }

  if (!body.workspaceId || !body.repoId) {
    return NextResponse.json(
      { error: "workspaceId and repoId are required.", code: "bad_request" },
      { status: 400 },
    );
  }

  try {
    const result = await proposeRepoConfig(body.workspaceId, body.repoId, {
      lanes: body.lanes,
      base_sha: body.base_sha,
      change_summary: body.change_summary,
      preset: body.preset ?? undefined,
    });
    return NextResponse.json(result);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json(
        { error: "Backend is unreachable.", code: "api_unavailable" },
        { status: 503 },
      );
    }
    if (err instanceof ApiHttpError) {
      const detail =
        err.detail && typeof err.detail === "object"
          ? (err.detail as Record<string, unknown>)
          : null;
      const code =
        detail && typeof detail.code === "string" ? detail.code : undefined;
      const message =
        detail && typeof detail.message === "string"
          ? detail.message
          : typeof err.detail === "string"
            ? err.detail
            : `HTTP ${err.status}`;
      return NextResponse.json(
        { error: message, code, detail },
        { status: err.status },
      );
    }
    return NextResponse.json(
      {
        error: err instanceof Error ? err.message : "Unknown error",
        code: "unknown",
      },
      { status: 500 },
    );
  }
}
