/**
 * POST /api/settings/agent-provider — set the workspace-bound agent
 * runtime (cursor / codex / claude).
 *
 * Server-action endpoint for the General settings card. Forwards to
 * PUT /v1/workspaces/{id}/agent-provider so the audit trail captures
 * the change with the dedicated ``workspace.agent_provider.set`` row
 * instead of a generic ``workspace.update``. The bound kind is what
 * shipctl run resolves on the GHA runner to pick which local CLI
 * (cursor-agent / codex / claude) to invoke.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  setAgentProvider,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

// Mirror of backend ``SUPPORTED_PROVIDERS``. Kept in sync by hand —
// the picker UI and this guard read the same set.
const VALID_PROVIDERS = new Set(["cursor", "codex", "claude"] as const);

type ProviderKind = "cursor" | "codex" | "claude";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const provider = (form.get("provider") ?? "").toString();

  if (!wsId || !VALID_PROVIDERS.has(provider as ProviderKind)) {
    return back(origin, "bad_input");
  }
  if (!isApiConfigured()) {
    return back(origin, "api_unavailable");
  }

  try {
    await setAgentProvider(wsId, provider as ProviderKind);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, "forbidden");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  return NextResponse.redirect(new URL("/settings/general", origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings/general", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
