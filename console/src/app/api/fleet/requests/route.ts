/**
 * Browser-side proxy for workspace-level fan-out dispatch
 * (``POST /v1/workspaces/{ws}/fleet/requests``).
 *
 * Lives as an app-router route so the session cookie stays on the
 * server and ``SHIP_API_URL`` never leaks into the client bundle —
 * same contract as ``/api/requests`` (the per-repo dispatcher), just
 * with an extra ``repo_ids[]`` array.
 */

import { NextResponse } from "next/server";

import {
  type ApiFleetRequestIn,
  ApiHttpError,
  ApiUnavailableError,
  createFleetRequest,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type RequestBody = ApiFleetRequestIn & {
  workspaceId: string;
};

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  let body: RequestBody;
  try {
    body = (await request.json()) as RequestBody;
  } catch {
    return NextResponse.json(
      { error: "Invalid JSON body.", code: "bad_request" },
      { status: 400 },
    );
  }

  if (!body.workspaceId) {
    return NextResponse.json(
      { error: "workspaceId is required.", code: "bad_request" },
      { status: 400 },
    );
  }
  if (!Array.isArray(body.repo_ids) || body.repo_ids.length === 0) {
    return NextResponse.json(
      { error: "Pick at least one repo.", code: "missing_repo_ids" },
      { status: 400 },
    );
  }

  const hasPatternShape = !!body.pattern_id;
  const hasAdhocShape = !!body.agent_slug && !!body.prompt;
  if (!hasPatternShape && !hasAdhocShape) {
    return NextResponse.json(
      {
        error:
          "Provide either a pattern_id (preferred) or both agent_slug and prompt.",
        code: "bad_request",
      },
      { status: 400 },
    );
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const payload: ApiFleetRequestIn = {
      pattern_id: body.pattern_id,
      inputs: body.inputs,
      agent_slug: body.agent_slug,
      prompt: body.prompt,
      context_ref: body.context_ref,
      repo_ids: body.repo_ids,
      title: body.title,
    };
    const result = await createFleetRequest(body.workspaceId, payload, token);
    return NextResponse.json(result, { status: 201 });
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
