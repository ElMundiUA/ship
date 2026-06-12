/**
 * Trigger a deployment of a repo.
 * POST /api/deploy   body: { workspaceId, repoId, llmProvider?, llmModel?, llmApiKey?, env? }
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  isApiConfigured,
  triggerDeploy,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  let body: {
    workspaceId?: string;
    repoId?: string;
    llmProvider?: string;
    llmModel?: string;
    llmApiKey?: string;
    region?: string;
    env?: { key: string; value: string; secret: boolean }[];
  };
  try {
    body = (await request.json()) as {
      workspaceId?: string;
      repoId?: string;
      llmProvider?: string;
      llmModel?: string;
      llmApiKey?: string;
      env?: { key: string; value: string; secret: boolean }[];
    };
  } catch {
    return NextResponse.json({ detail: "invalid_json" }, { status: 400 });
  }
  if (!body.workspaceId || !body.repoId) {
    return NextResponse.json(
      { detail: "workspaceId and repoId are required" },
      { status: 400 },
    );
  }
  try {
    const token = (await getSessionToken()) ?? undefined;
    const llm =
      body.llmProvider || body.llmModel || body.llmApiKey
        ? {
            provider: body.llmProvider,
            model: body.llmModel,
            apiKey: body.llmApiKey,
          }
        : null;
    const data = await triggerDeploy(
      body.workspaceId,
      body.repoId,
      llm,
      body.env ?? [],
      token,
      body.region,
    );
    return NextResponse.json(data, { status: 202 });
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.detail ?? err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
