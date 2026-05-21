/**
 * POST /api/inbox/[id]/decide — structured operator decision on a row
 * carrying ``payload.action_items`` (ELS-159 / ELS-145 per-item binary).
 *
 * Form POST (mailbox footer) → 303 redirect. JSON POST (InboxActionPanel)
 * → ``InboxItemDetail`` body.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  decideInboxItem,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

type DecideBody = {
  ws?: string;
  workspaceId?: string;
  selections?: string[];
  freeform?: string | null;
  action_item_id?: string | null;
  choice?: "primary" | "secondary" | null;
};

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const origin = resolveOrigin(request);
  const { id } = await params;
  const wantsJson =
    request.headers.get("accept")?.includes("application/json") ||
    request.headers.get("content-type")?.includes("application/json");

  if (wantsJson) {
    return handleJson(request, origin, id);
  }
  return handleForm(request, origin, id);
}

async function handleJson(request: Request, origin: string, id: string) {
  let body: DecideBody;
  try {
    body = (await request.json()) as DecideBody;
  } catch {
    return NextResponse.json({ error: "bad_input" }, { status: 400 });
  }

  const wsId = (body.ws ?? body.workspaceId ?? "").toString();
  if (!wsId || !id) {
    return NextResponse.json({ error: "bad_input" }, { status: 400 });
  }
  if (!isApiConfigured()) {
    return NextResponse.json({ error: "api_unavailable" }, { status: 503 });
  }

  const decideBody: Parameters<typeof decideInboxItem>[2] = {};
  const actionItemId = (body.action_item_id ?? "").toString().trim();
  if (actionItemId) {
    const choice = body.choice;
    if (choice !== "primary" && choice !== "secondary") {
      return NextResponse.json({ error: "bad_input" }, { status: 400 });
    }
    decideBody.action_item_id = actionItemId;
    decideBody.choice = choice;
  } else {
    const selections = Array.isArray(body.selections)
      ? body.selections.filter((s): s is string => typeof s === "string")
      : [];
    const freeform =
      typeof body.freeform === "string" ? body.freeform.trim() : "";
    if (selections.length === 0 && !freeform) {
      return NextResponse.json({ error: "bad_input" }, { status: 400 });
    }
    decideBody.selections = selections;
    decideBody.freeform = freeform || null;
  }

  try {
    const item = await decideInboxItem(wsId, id, decideBody);
    return NextResponse.json(item);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?error=session_expired", origin),
        303,
      );
    }
    const code = codeFor(err);
    const status =
      err instanceof ApiHttpError
        ? err.status
        : err instanceof ApiUnavailableError
          ? 503
          : 500;
    return NextResponse.json({ error: code }, { status });
  }
}

async function handleForm(request: Request, origin: string, id: string) {
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const actionItemId = (form.get("action_item_id") ?? "").toString().trim();
  const choiceRaw = (form.get("choice") ?? "").toString().trim();
  const returnToRaw = (form.get("return_to") ?? "").toString();
  const returnTo =
    returnToRaw.startsWith("/") && !returnToRaw.startsWith("//")
      ? returnToRaw
      : null;

  if (!wsId || !id) return back(origin, id, "bad_input", returnTo);
  if (!isApiConfigured()) return back(origin, id, "api_unavailable", returnTo);

  if (actionItemId) {
    if (choiceRaw !== "primary" && choiceRaw !== "secondary") {
      return back(origin, id, "bad_input", returnTo);
    }
    try {
      await decideInboxItem(wsId, id, {
        action_item_id: actionItemId,
        choice: choiceRaw,
      });
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      return back(origin, id, codeFor(err), returnTo);
    }
    const successTarget = returnTo ?? `/inbox/${id}`;
    return NextResponse.redirect(new URL(successTarget, origin), 303);
  }

  return back(origin, id, "bad_input", returnTo);
}

function back(origin: string, id: string, code: string, returnTo: string | null) {
  const url = new URL(returnTo ?? `/inbox/${id}`, origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}

function codeFor(err: unknown): string {
  if (err instanceof ApiUnavailableError) return "api_unavailable";
  if (err instanceof ApiHttpError) {
    if (err.status === 403) return "forbidden";
    if (err.status === 404) return "not_found";
    if (err.status === 409) return "state_invalid";
    if (err.status === 422) return "validation_failed";
    return `http_${err.status}`;
  }
  return "unknown";
}
