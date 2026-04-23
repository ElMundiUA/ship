/**
 * POST /api/runs/[id]/rerun — dispatch a fresh run of the same pipeline.
 *
 * Server-action endpoint behind the run-detail page's "Re-run"
 * button (RFC-0010 P3-05). Forwards to the existing dispatch
 * endpoint:
 *
 *   POST /v1/workspaces/{ws}/pipelines/{pipeline_id}/runs
 *
 * with a ``note`` of ``"Re-run of <run_id>"`` so the audit trail
 * carries the lineage. The button forwards the resolved
 * ``pipeline_id`` server-side (page already knows it from
 * ``getRunDetail``) so this handler doesn't have to repeat the
 * pipeline lookup.
 *
 * On success: 303 back to the run-detail page so the operator
 * stays in context. On failure: 303 back with ``?error=<code>``
 * so the run-detail page surfaces a banner via the same
 * convention as the inbox handlers.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  runPipeline,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const origin = resolveOrigin(request);
  const { id: runId } = await params;
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const pipelineId = (form.get("pipeline") ?? "").toString();

  if (!wsId || !runId || !pipelineId) {
    return back(origin, runId, "bad_input");
  }
  if (!isApiConfigured()) return back(origin, runId, "api_unavailable");

  try {
    await runPipeline(wsId, pipelineId, `Re-run of ${runId}`);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?error=session_expired", origin),
        303,
      );
    }
    return back(origin, runId, codeFor(err));
  }

  // Bounce back to the source run's detail page. The dispatched
  // run will appear in /runs once the backend lands its row;
  // operators commonly want to see the original outcome again
  // (and the new run shows up in the right rail's "Recent runs"
  // surface that the parent /runs list owns).
  const url = new URL(`/runs/${encodeURIComponent(runId)}`, origin);
  url.searchParams.set("rerun", "dispatched");
  return NextResponse.redirect(url, 303);
}

function back(origin: string, runId: string, code: string) {
  const url = new URL(`/runs/${encodeURIComponent(runId)}`, origin);
  url.searchParams.set("rerun_error", code);
  return NextResponse.redirect(url, 303);
}

function codeFor(err: unknown): string {
  if (err instanceof ApiUnavailableError) return "api_unavailable";
  if (err instanceof ApiHttpError) {
    if (err.status === 403) return "forbidden";
    if (err.status === 404) return "missing";
    if (err.status === 409) return "disabled";
    if (err.status === 412) {
      const code =
        err.detail && typeof err.detail === "object" && "code" in err.detail
          ? String((err.detail as { code: unknown }).code)
          : "precondition";
      return `precondition_${code}`;
    }
    if (err.status === 502) return "dispatch_failed";
    return `http_${err.status}`;
  }
  return "unknown";
}
