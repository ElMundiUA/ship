/**
 * POST /api/integrations/delete — disconnect an integration entirely.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteIntegration,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const kind = (form.get("kind") ?? "").toString();
  if (!wsId || !kind) {
    return NextResponse.redirect(new URL("/integrations?error=bad_input", origin), 303);
  }
  if (!isApiConfigured()) {
    return NextResponse.redirect(
      new URL("/integrations?error=api_unavailable", origin),
      303,
    );
  }

  try {
    await deleteIntegration(wsId, kind);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
    }
    if (err instanceof ApiUnavailableError) {
      return NextResponse.redirect(
        new URL("/integrations?error=api_unavailable", origin),
        303,
      );
    }
    return NextResponse.redirect(new URL("/integrations?error=unknown", origin), 303);
  }

  return NextResponse.redirect(new URL("/integrations", origin), 303);
}
