/**
 * Proxy for the Requests dispatcher (``/requests`` catalog form).
 *
 * Accepts JSON from the client-side Requests form and forwards to
 * ``POST /v1/workspaces/{ws}/repos/{repo}/requests``. Lives as an
 * app-router API route (rather than going direct from the browser)
 * so the session cookie is available and the backend URL never leaks
 * into client bundles.
 *
 * RFC-0008 C4 — the form now prefers the pattern-backed shape
 * (``{pattern_id, inputs}``) but legacy free-form dispatches
 * (``{agent_slug, prompt}``) still round-trip through the same route
 * so older clients keep working during the catalog transition.
 */

import { NextResponse } from "next/server";

import {
  type ApiAgentRequestIn,
  ApiHttpError,
  ApiUnavailableError,
  dispatchAgentRequest,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type RequestBody = ApiAgentRequestIn & {
  workspaceId: string;
  repoId: string;
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

  if (!body.workspaceId || !body.repoId) {
    return NextResponse.json(
      { error: "workspaceId and repoId are required.", code: "bad_request" },
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
    const payload: ApiAgentRequestIn = {
      pattern_id: body.pattern_id,
      inputs: body.inputs,
      agent_slug: body.agent_slug,
      prompt: body.prompt,
      context_ref: body.context_ref,
    };
    const result = await dispatchAgentRequest(
      body.workspaceId,
      body.repoId,
      payload,
      token,
    );
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
