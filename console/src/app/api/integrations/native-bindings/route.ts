import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  replaceNativeBindings,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const installationId = (form.get("installation_id") ?? "").toString();
  const externalIds = form
    .getAll("external_id")
    .map((value) => value.toString())
    .filter(Boolean);

  if (!wsId || !installationId) {
    return redirectWithError(origin, "missing_native_binding_target");
  }
  if (!isApiConfigured()) {
    return redirectWithError(origin, "api_unavailable");
  }

  try {
    await replaceNativeBindings(wsId, installationId, {
      resource_type: "repo",
      external_ids: externalIds,
    });
    const url = new URL("/integrations", origin);
    url.searchParams.set("native", "bindings_saved");
    return NextResponse.redirect(url, 303);
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
