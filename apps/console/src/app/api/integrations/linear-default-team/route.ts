/**
 * POST /api/integrations/linear-default-team
 *
 * Body: { workspace_id, team_id, team_key }
 *
 * Reads the workspace's existing Linear ``Integration`` row, merges
 * ``team_id`` + ``team_key`` into ``config`` (preserving everything
 * else — ``team_options``, ``label_id_by_stage``, etc.), and PUTs
 * back via ``upsertIntegration``. Used by the integrations page's
 * Linear default-team picker (ELS-71 part 2).
 *
 * Why merge server-side: the FE has ``i.config`` in scope, but the
 * backend's PUT replaces ``config`` wholesale. Doing the merge in
 * this route means the picker can send only the two keys it cares
 * about and not worry about wiping ``team_options`` / FSM maps.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listIntegrations,
  upsertIntegration,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return json({ error: "api_unavailable" }, 503);
  }
  const body = (await request.json().catch(() => null)) as
    | {
        workspace_id?: string;
        team_id?: string;
        team_key?: string;
      }
    | null;
  if (!body?.workspace_id || !body.team_id || !body.team_key) {
    return json({ error: "bad_request" }, 400);
  }

  const token = (await getSessionToken()) ?? undefined;
  try {
    const integrations = await listIntegrations(body.workspace_id, token);
    const linear = integrations.find((i) => i.kind === "linear");
    if (!linear) {
      return json({ error: "linear_not_connected" }, 404);
    }
    const merged = {
      ...(linear.config ?? {}),
      team_id: body.team_id,
      team_key: body.team_key,
    };
    const updated = await upsertIntegration(
      body.workspace_id,
      "linear",
      { config: merged, secret: null },
      token,
    );
    return json({ integration: updated }, 200);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return json({ error: "api_unavailable" }, 502);
    if (err instanceof ApiHttpError) {
      if (err.status === 401) return json({ error: "session_expired" }, 401);
      if (err.status === 403) return json({ error: "forbidden" }, 403);
      return json({ error: `http_${err.status}` }, err.status);
    }
    return json({ error: "unknown" }, 500);
  }
}

function json(body: unknown, status: number) {
  return NextResponse.json(body, { status });
}
