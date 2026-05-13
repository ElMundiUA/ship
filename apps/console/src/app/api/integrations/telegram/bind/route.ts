/**
 * Telegram bind confirm proxy.
 *
 * Receives the form post from ``/integrations/telegram/bind``,
 * forwards to backend ``/v1/integrations/telegram/bind/confirm``,
 * and redirects the user to ``/settings/integrations`` (with a
 * success or error flag) so the bind result is visible alongside
 * the rest of the workspace's connections.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  confirmTelegramBind,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const nonce = (form.get("nonce") ?? "").toString();
  const workspaceId = (form.get("workspace_id") ?? "").toString();

  if (!nonce) return back(origin, "", "invalid");
  if (!workspaceId) return back(origin, nonce, "invalid");
  if (!isApiConfigured()) return back(origin, nonce, "api_unavailable");

  try {
    const link = await confirmTelegramBind({
      nonce,
      workspace_id: workspaceId,
    });
    const success = new URL("/settings/integrations", origin);
    success.searchParams.set("ws", link.workspace_id);
    success.searchParams.set("telegram_bound", "1");
    return NextResponse.redirect(success, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, nonce, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        const next = `/integrations/telegram/bind?nonce=${encodeURIComponent(nonce)}`;
        return NextResponse.redirect(
          new URL(`/login?next=${encodeURIComponent(next)}`, origin),
          303,
        );
      }
      if (err.status === 403) return back(origin, nonce, "forbidden");
      if (err.status === 400) return back(origin, nonce, "invalid");
      if (err.status === 410) return back(origin, nonce, "expired");
      return back(origin, nonce, `http_${err.status}`);
    }
    return back(origin, nonce, "unknown");
  }
}

function back(origin: string, nonce: string, reason: string) {
  const url = new URL("/integrations/telegram/bind", origin);
  if (nonce) url.searchParams.set("nonce", nonce);
  url.searchParams.set("error", reason);
  return NextResponse.redirect(url, 303);
}
