/**
 * POST /api/integrations/native-azure-devops — connect Azure DevOps via PAT.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  connectAzureDevOpsPat,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const organization = (form.get("organization") ?? "").toString().trim();
  const project = (form.get("project") ?? "").toString().trim();
  const pat = (form.get("pat") ?? "").toString();
  const next = (form.get("next") ?? "/integrations").toString();

  if (!wsId || !organization || !pat) {
    return back(origin, next, "bad_input");
  }
  if (!isApiConfigured()) return back(origin, next, "api_unavailable");

  try {
    await connectAzureDevOpsPat(wsId, {
      organization,
      project: project || null,
      pat,
      scopes: ["vso.code", "vso.build_execute"],
    });
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
  url.searchParams.set("azure_devops", "connected");
  return NextResponse.redirect(url, 303);
}

function back(origin: string, next: string, code: string) {
  const url = new URL(next.startsWith("/") ? next : "/integrations", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
