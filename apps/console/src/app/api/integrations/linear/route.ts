import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  startLinearInstall,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();

  if (!wsId) {
    return redirectWithError(origin, "missing_workspace");
  }
  if (!isApiConfigured()) {
    return redirectWithError(origin, "api_unavailable");
  }

  try {
    const start = await startLinearInstall(wsId);
    return NextResponse.redirect(start.install_url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return redirectWithError(origin, "api_unavailable");
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      if (err.status === 503) {
        return redirectWithError(origin, "not_configured_linear");
      }
      return redirectWithError(origin, `http_${err.status}`);
    }
    return redirectWithError(origin, "unknown");
  }
}

function redirectWithError(origin: string, error: string) {
  const url = new URL("/integrations", origin);
  url.searchParams.set("error", error);
  return NextResponse.redirect(url, 303);
}
