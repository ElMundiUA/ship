/**
 * Browser-side proxy for creating a workspace agent role row
 * (``POST /v1/workspaces/{ws}/agent-roles``).
 *
 * Two semantics — same endpoint:
 *
 * * **Override** — ``slug`` matches a Ship default; the new row's
 *   prompt shadows the default for this workspace. ``base_role_slug``
 *   must be omitted / null.
 * * **Clone** — ``slug`` is a new identifier; ``base_role_slug``
 *   records which Ship default seeded the body.
 *
 * The error envelope mirrors the policies proxy so the client only
 * needs one parser.
 */

import { NextResponse } from "next/server";

import {
  type ApiAgentRoleCreateIn,
  ApiHttpError,
  ApiUnavailableError,
  createWorkspaceAgentRole,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type Body = ApiAgentRoleCreateIn & { workspaceId: string };

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
  if (!body.slug?.trim() || !body.name?.trim() || !body.prompt?.trim()) {
    return NextResponse.json(
      {
        error: "slug, name, and prompt are all required.",
        code: "bad_request",
      },
      { status: 400 },
    );
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const payload: ApiAgentRoleCreateIn = {
      slug: body.slug,
      name: body.name,
      prompt: body.prompt,
      base_role_slug: body.base_role_slug ?? null,
    };
    const result = await createWorkspaceAgentRole(
      body.workspaceId,
      payload,
      token,
    );
    return NextResponse.json(result, { status: 201 });
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
