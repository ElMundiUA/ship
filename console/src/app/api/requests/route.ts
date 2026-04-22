/**
 * Proxy for the Requests dispatcher (``/requests`` New request form).
 *
 * Accepts JSON from ``new-request.tsx`` and forwards to the backend
 * ``POST /v1/workspaces/{ws}/repos/{repo}/requests``. Lives as an
 * app-router API route (rather than going direct from the browser)
 * so the session cookie is available and the backend URL never leaks
 * into client bundles.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  dispatchAgentRequest,
  isApiConfigured,
} from "@/lib/api/client";

type RequestBody = {
  workspaceId: string;
  repoId: string;
  agent_slug: string;
  prompt: string;
  context_ref?: string;
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
  if (!body.agent_slug || !body.prompt) {
    return NextResponse.json(
      {
        error: "agent_slug and prompt are required.",
        code: "bad_request",
      },
      { status: 400 },
    );
  }

  try {
    const result = await dispatchAgentRequest(body.workspaceId, body.repoId, {
      agent_slug: body.agent_slug,
      prompt: body.prompt,
      context_ref: body.context_ref,
    });
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
