/**
 * Dashboard form handler — dismiss a notification banner.
 *
 * Form sends ``ws`` and either ``notification`` (single row) or
 * ``all=1`` (the "Clear all" link). We proxy to the backend dismiss
 * endpoint(s) and bounce back to the dashboard. Rendered as a plain
 * ``<form method="POST">`` so the banner rail keeps working without
 * client JS — graceful degradation matters here because onboarding
 * users hit this before any pipeline is live.
 *
 * Errors get swallowed quietly: a failed dismiss just leaves the
 * banner where it was, which is the least-surprising outcome for a
 * best-effort UX action.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  dismissAllNotifications,
  dismissNotification,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const notifId = (form.get("notification") ?? "").toString();
  const all = (form.get("all") ?? "").toString() === "1";

  if (!wsId) {
    return NextResponse.redirect(new URL("/", origin), 303);
  }
  if (!isApiConfigured()) {
    return back(origin, wsId, "api_unavailable");
  }

  try {
    if (all) {
      await dismissAllNotifications(wsId);
    } else if (notifId) {
      await dismissNotification(wsId, notifId);
    } else {
      return back(origin, wsId, "missing");
    }
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, wsId, "forbidden");
      if (err.status === 404) return back(origin, wsId, "missing");
      return back(origin, wsId, `http_${err.status}`);
    }
    return back(origin, wsId, "unknown");
  }

  // No banner on happy path — the dismissed banner already disappeared,
  // re-rendering a "you dismissed it" callout would be noise.
  return NextResponse.redirect(new URL(`/?ws=${wsId}`, origin), 303);
}

function back(origin: string, wsId: string, reason: string) {
  const url = new URL("/", origin);
  url.searchParams.set("ws", wsId);
  url.searchParams.set("notification_dismiss", "error");
  url.searchParams.set("reason", reason);
  return NextResponse.redirect(url, 303);
}
