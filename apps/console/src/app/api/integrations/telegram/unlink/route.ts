/**
 * POST /api/integrations/telegram/unlink — disconnect a bound TG group.
 *
 * Triggered from the Telegram links section in workspace settings.
 * Forwards to backend ``DELETE /v1/integrations/telegram/links/{id}``,
 * which revokes the service PAT *before* deleting the link row, so
 * the bot stops answering immediately.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteTelegramLink,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const linkId = (form.get("link_id") ?? "").toString();
  const wsId = (form.get("ws") ?? "").toString();

  const target = new URL("/settings/integrations/telegram", origin);
  if (wsId) target.searchParams.set("ws", wsId);

  if (!linkId) {
    target.searchParams.set("error", "bad_input");
    return NextResponse.redirect(target, 303);
  }
  if (!isApiConfigured()) {
    target.searchParams.set("error", "api_unavailable");
    return NextResponse.redirect(target, 303);
  }

  try {
    await deleteTelegramLink(linkId);
    target.searchParams.set("unlinked", "1");
    return NextResponse.redirect(target, 303);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?error=session_expired", origin),
        303,
      );
    }
    if (err instanceof ApiUnavailableError) {
      target.searchParams.set("error", "api_unavailable");
      return NextResponse.redirect(target, 303);
    }
    if (err instanceof ApiHttpError) {
      target.searchParams.set("error", `http_${err.status}`);
      return NextResponse.redirect(target, 303);
    }
    target.searchParams.set("error", "unknown");
    return NextResponse.redirect(target, 303);
  }
}
