/**
 * Wizard seed activation — merges the just-opened seed PR via the
 * App's installation token. Powers the "Activate Ship now" modal in
 * the confirm step.
 *
 * Caller passes ``{workspace_id, repo_id, pr_number}``. The backend
 * uses the same App install that opened the PR, so no extra auth
 * is needed beyond the operator's session.
 *
 * Branch-protection / required-status failures bubble up as a 409
 * with ``error: "merge_blocked"`` so the FE can render the
 * "I'll merge it myself" fallback without surfacing an error toast.
 */

import { NextResponse } from "next/server";

import {
  activateWizardSeed,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return json({ error: "api_unavailable" }, 503);
  }

  const body = (await request.json().catch(() => null)) as
    | {
        workspace_id?: string;
        repo_id?: string;
        pr_number?: number;
      }
    | null;

  if (!body?.workspace_id || !body.repo_id || !body.pr_number) {
    return json({ error: "bad_request" }, 400);
  }

  const token = (await getSessionToken()) ?? undefined;
  try {
    const result = await activateWizardSeed(
      body.workspace_id,
      body.repo_id,
      body.pr_number,
      token,
    );
    return json({ result }, 200);
  } catch (err) {
    return relayError(err);
  }
}

function json(body: unknown, status: number) {
  return NextResponse.json(body, { status });
}

function relayError(err: unknown) {
  if (err instanceof ApiUnavailableError) return json({ error: "api_unavailable" }, 502);
  if (err instanceof ApiHttpError) {
    if (err.status === 401) return json({ error: "session_expired" }, 401);
    if (err.status === 403) return json({ error: "forbidden" }, 403);
    if (err.status === 404) return json({ error: "not_found" }, 404);
    if (err.status === 409) {
      const detail = err.detail as { code?: string; github_message?: string } | null;
      return json(
        {
          error: detail?.code ?? "merge_blocked",
          github_message: detail?.github_message,
        },
        409,
      );
    }
    if (err.status === 412) {
      const detail = err.detail as { code?: string } | null;
      return json({ error: detail?.code ?? "precondition_failed" }, 412);
    }
    if (err.status === 502) {
      const detail = err.detail as { code?: string; github_message?: string } | null;
      return json(
        {
          error: detail?.code ?? "github_api_error",
          github_message: detail?.github_message,
        },
        502,
      );
    }
    return json({ error: `http_${err.status}` }, err.status);
  }
  return json({ error: "unknown" }, 500);
}
