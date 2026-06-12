import { NextResponse } from "next/server";

import {
  ApiHttpError,
  isApiConfigured,
  setDeployPlannerPref,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Persist a repo's deploy-planner preference (provider + model).
 * POST /api/deploy/planner-pref
 *   body: { ws, repoId, provider, model }
 */
export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  let body: {
    ws?: string;
    repoId?: string;
    provider?: string | null;
    model?: string | null;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ detail: "invalid_json" }, { status: 400 });
  }
  if (!body.ws || !body.repoId) {
    return NextResponse.json(
      { detail: "ws and repoId are required" },
      { status: 400 },
    );
  }
  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await setDeployPlannerPref(
      body.ws,
      body.repoId,
      { provider: body.provider ?? null, model: body.model ?? null },
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
