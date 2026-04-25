/**
 * POST /api/integrations/native-delete — disable a native provider install.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteNativeIntegration,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const installationId = (form.get("installation_id") ?? "").toString();
  const next = (form.get("next") ?? "/integrations").toString();

  if (!wsId || !installationId) {
    return back(origin, next, "bad_input");
  }
  if (!isApiConfigured()) return back(origin, next, "api_unavailable");

  try {
    await deleteNativeIntegration(wsId, installationId);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return back(origin, next, "api_unavailable");
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      return back(origin, next, `http_${err.status}`);
    }
    return back(origin, next, "unknown");
  }

  const url = new URL(next.startsWith("/") ? next : "/integrations", origin);
  url.searchParams.set("native", "disabled");
  return NextResponse.redirect(url, 303);
}

function back(origin: string, next: string, code: string) {
  const url = new URL(next.startsWith("/") ? next : "/integrations", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
