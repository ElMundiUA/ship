/**
 * Browser-side proxy for the LLM pattern-draft endpoint
 * (``POST /v1/workspaces/{ws}/patterns/draft``).
 *
 * Kept server-side so the session bearer never leaves the cookie;
 * the AI author modal posts a free-form brief here and renders the
 * returned :class:`ApiPatternDraft` inline for review before the
 * operator hits save.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  draftCustomPattern,
  isApiConfigured,
  type ApiPatternDraftIn,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type Body = ApiPatternDraftIn & { workspaceId: string };

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  let body: Body;
  try {
    body = (await request.json()) as Body;
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
  const prompt = (body.prompt ?? "").trim();
  if (prompt.length < 8) {
    return NextResponse.json(
      {
        error: "Describe the pattern in a sentence or two (>= 8 chars).",
        code: "bad_request",
      },
      { status: 400 },
    );
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const draft = await draftCustomPattern(
      body.workspaceId,
      {
        prompt,
        target_modes: body.target_modes,
      },
      token,
    );
    return NextResponse.json(draft, { status: 200 });
  } catch (err) {
    return errorResponse(err);
  }
}

function errorResponse(err: unknown): NextResponse {
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
