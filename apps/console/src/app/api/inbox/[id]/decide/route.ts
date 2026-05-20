/**
 * POST /api/inbox/[id]/decide — operator's structured pick on an
 * action_items-bearing row (Inbox Decision UI, ELS-159).
 *
 * Server-side bridge so InboxActionPanel (a "use client" component)
 * doesn't import the server-only ``@/lib/api/client`` module directly.
 * The build crashed before this route landed because the panel pulled
 * ``decideInboxItem`` straight from client.ts, which is tagged
 * ``import "server-only"`` — Next.js refused to bundle the client
 * component once it transitively reached the server-only file.
 *
 * Accepts JSON ``{selections: string[], freeform: string | null}`` and
 * returns the refreshed ``InboxItemDetail`` (or 4xx with an
 * ``error_code``) so the panel can update local state in-place.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  decideInboxItem,
  isApiConfigured,
} from "@/lib/api/client";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error_code: "api_unavailable" },
      { status: 503 },
    );
  }
  let body: { workspaceId?: string; selections?: unknown; freeform?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error_code: "bad_input" }, { status: 400 });
  }

  const wsId = typeof body.workspaceId === "string" ? body.workspaceId : "";
  const selections = Array.isArray(body.selections)
    ? body.selections.filter((s): s is string => typeof s === "string")
    : [];
  const freeform =
    typeof body.freeform === "string" && body.freeform.trim().length > 0
      ? body.freeform
      : null;

  if (!wsId || !id) {
    return NextResponse.json({ error_code: "bad_input" }, { status: 400 });
  }
  if (selections.length === 0 && !freeform) {
    return NextResponse.json({ error_code: "decide_empty" }, { status: 422 });
  }

  try {
    const next = await decideInboxItem(wsId, id, { selections, freeform });
    return NextResponse.json(next);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.json(
          { error_code: "session_expired" },
          { status: 401 },
        );
      }
      return NextResponse.json(
        { error_code: codeFor(err) },
        { status: err.status },
      );
    }
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json(
        { error_code: "api_unavailable" },
        { status: 503 },
      );
    }
    return NextResponse.json({ error_code: "unknown" }, { status: 500 });
  }
}

function codeFor(err: ApiHttpError): string {
  if (err.status === 403) return "forbidden";
  if (err.status === 404) return "not_found";
  if (err.status === 409) return "state_invalid";
  if (err.status === 422) return "validation_failed";
  return `http_${err.status}`;
}
