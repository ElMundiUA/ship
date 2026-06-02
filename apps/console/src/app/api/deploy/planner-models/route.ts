import { NextResponse } from "next/server";

import {
  ApiHttpError,
  getDeployPlannerModels,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * List selectable planner models for a provider.
 * POST /api/deploy/planner-models
 *   body: { ws, repoId, provider, apiKey? }
 *
 * POST (not GET) so the optional plaintext key rides in the body rather
 * than a URL that could land in access logs.
 */
export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  let body: {
    ws?: string;
    repoId?: string;
    provider?: string;
    apiKey?: string;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ detail: "invalid_json" }, { status: 400 });
  }
  if (!body.ws || !body.repoId || !body.provider) {
    return NextResponse.json(
      { detail: "ws, repoId and provider are required" },
      { status: 400 },
    );
  }
  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await getDeployPlannerModels(
      body.ws,
      body.repoId,
      body.provider,
      body.apiKey,
      token,
    );
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.detail ?? err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
