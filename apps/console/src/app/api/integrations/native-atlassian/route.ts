/**
 * POST /api/integrations/native-atlassian — connect Jira + Confluence.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  connectAtlassianApiToken,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const site = (form.get("site") ?? "").toString().trim();
  const email = (form.get("email") ?? "").toString().trim();
  const apiToken = (form.get("api_token") ?? "").toString();
  const jiraProject = (form.get("jira_project") ?? "").toString().trim();
  const next = (form.get("next") ?? "/integrations").toString();

  if (!wsId || !site || !email || !apiToken) {
    return back(origin, next, "bad_input");
  }
  if (!isApiConfigured()) return back(origin, next, "api_unavailable");

  try {
    await connectAtlassianApiToken(wsId, {
      site,
      email,
      api_token: apiToken,
      jira_project: jiraProject || null,
    });
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, next, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      return back(origin, next, `http_${err.status}`);
    }
    return back(origin, next, "unknown");
  }

  const url = new URL(next.startsWith("/") ? next : "/integrations", origin);
  url.searchParams.set("atlassian", "connected");
  return NextResponse.redirect(url, 303);
}

function back(origin: string, next: string, code: string) {
  const url = new URL(next.startsWith("/") ? next : "/integrations", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
