/**
 * Server-side proxy for the promotion persistence endpoint (PR-7B).
 *
 * ``POST /v1/workspaces/{ws}/knowledge/promote`` — creates the
 * workspace-scope bucket + article and optionally marks source
 * repo-scope articles as overrides. Called from the promotion
 * modal's Promote button.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  promoteKnowledge,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type Body = {
  workspaceId?: string;
  slug?: string;
  title?: string;
  body?: string;
  summary?: string | null;
  sourceArticleIds?: string[];
  markSourcesAsOverrides?: boolean;
};

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  let payload: Body;
  try {
    payload = (await request.json()) as Body;
  } catch {
    return NextResponse.json(
      { error: "Invalid JSON body.", code: "bad_request" },
      { status: 400 },
    );
  }

  const workspaceId = payload.workspaceId;
  const slug = (payload.slug ?? "").trim();
  const title = (payload.title ?? "").trim();
  const body = (payload.body ?? "").trim();
  if (!workspaceId || !slug || !title || !body) {
    return NextResponse.json(
      {
        error: "workspaceId, slug, title, and body are all required.",
        code: "bad_request",
      },
      { status: 400 },
    );
  }
  const sourceArticleIds = Array.isArray(payload.sourceArticleIds)
    ? payload.sourceArticleIds.filter((id): id is string => typeof id === "string")
    : [];

  try {
    const token = (await getSessionToken()) ?? undefined;
    const resp = await promoteKnowledge(
      workspaceId,
      {
        slug,
        title,
        body,
        summary: payload.summary ?? null,
        sourceArticleIds,
        markSourcesAsOverrides: payload.markSourcesAsOverrides ?? true,
      },
      token,
    );
    return NextResponse.json(resp, { status: 200 });
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
