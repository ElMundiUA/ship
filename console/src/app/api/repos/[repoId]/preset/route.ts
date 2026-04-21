/**
 * Per-repo preset mutation — wizard v2 step 4 (configure).
 *
 * The RepoCard client component posts ``{ workspace_id, preset }``
 * and gets back the fresh ``ApiActivatedRepo`` row. Unlike the
 * dashboard handler (``/api/dashboard/update-preset``) we don't
 * 303-redirect — the card patches its own state from the response.
 *
 * We intentionally never pass ``reshape`` from the wizard: the
 * seed PR handler will materialise the lanes from the chosen
 * preset on the first seed, and we don't want to silently rewire
 * anything on preset changes mid-wizard.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  updateRepo,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ repoId: string }> },
) {
  if (!isApiConfigured()) {
    return NextResponse.json({ error: "api_unavailable" }, { status: 503 });
  }

  const { repoId } = await params;
  const body = (await request.json().catch(() => null)) as
    | { workspace_id?: string; preset?: string | null }
    | null;

  if (!body?.workspace_id || !repoId) {
    return NextResponse.json({ error: "bad_request" }, { status: 400 });
  }

  const token = (await getSessionToken()) ?? undefined;
  try {
    const result = await updateRepo(
      body.workspace_id,
      repoId,
      { preset: body.preset ?? null },
      { token },
    );
    return NextResponse.json({ repo: result }, { status: 200 });
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json({ error: "api_unavailable" }, { status: 502 });
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.json({ error: "session_expired" }, { status: 401 });
      }
      if (err.status === 403) {
        return NextResponse.json({ error: "forbidden" }, { status: 403 });
      }
      if (err.status === 404) {
        return NextResponse.json({ error: "not_found" }, { status: 404 });
      }
      if (err.status === 422) {
        return NextResponse.json({ error: "bad_preset" }, { status: 422 });
      }
      return NextResponse.json(
        { error: `http_${err.status}` },
        { status: err.status },
      );
    }
    return NextResponse.json({ error: "unknown" }, { status: 500 });
  }
}
