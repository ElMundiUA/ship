/**
 * POST /api/oauth/grant — the consent screen's Approve / Deny action.
 *
 * Approve: call the backend grant endpoint (under the operator's
 * session) to mint a single-use PKCE-bound authorization code, then
 * 302 the browser to the client's loopback `redirect_uri` with the
 * code + state. Deny: 302 back with `error=access_denied`. Either way
 * the operator never sees or pastes a token — the MCP client picks the
 * code up on its loopback listener and exchanges it for the access
 * token at the backend token endpoint.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  oauthAuthorizeGrant,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { resolveOrigin } from "@/lib/api/origin";

function field(form: FormData, key: string): string {
  const v = form.get(key);
  return typeof v === "string" ? v.trim() : "";
}

/** Only bounce to same-shape client redirect URIs: http(s) with a code
 * or error query. Guards against open-redirect via a forged form. */
function safeRedirectTarget(uri: string): URL | null {
  try {
    const u = new URL(uri);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return u;
  } catch {
    return null;
  }
}

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const decision = field(form, "decision");
  const redirectUri = field(form, "redirect_uri");
  const state = field(form, "state");

  const target = safeRedirectTarget(redirectUri);
  if (!target) {
    // Can't safely bounce anywhere — surface the error in-console.
    return backToConsent(origin, "invalid_redirect_uri");
  }

  if (decision === "deny") {
    target.searchParams.set("error", "access_denied");
    if (state) target.searchParams.set("state", state);
    return NextResponse.redirect(target.toString(), 303);
  }

  if (!isApiConfigured()) return backToConsent(origin, "api_unavailable");
  const token = (await getSessionToken()) ?? undefined;

  try {
    const { redirect_to } = await oauthAuthorizeGrant(
      {
        client_id: field(form, "client_id"),
        redirect_uri: redirectUri,
        code_challenge: field(form, "code_challenge"),
        code_challenge_method: field(form, "code_challenge_method") || "S256",
        state: state || null,
        scope: field(form, "scope") || null,
        workspace_id: field(form, "workspace_id"),
      },
      token,
    );
    return NextResponse.redirect(redirect_to, 303);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?reason=session_expired", origin),
        303,
      );
    }
    // Per OAuth, surface failures to the client via the redirect so the
    // agent shows a real error rather than hanging on its listener.
    const code =
      err instanceof ApiUnavailableError
        ? "temporarily_unavailable"
        : err instanceof ApiHttpError && err.status === 403
          ? "access_denied"
          : "server_error";
    target.searchParams.set("error", code);
    if (state) target.searchParams.set("state", state);
    return NextResponse.redirect(target.toString(), 303);
  }
}

function backToConsent(origin: string, error: string) {
  const url = new URL("/", origin);
  url.searchParams.set("oauth_error", error);
  return NextResponse.redirect(url, 303);
}
