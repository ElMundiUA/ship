"use client";

/**
 * Per-repo "SDLC readiness" card (BS2-FE).
 *
 * Lazy by design: renders only a "Check readiness" button until the
 * operator clicks, because each assessment is a couple of GitHub API
 * calls server-side (so N eager cards would be N×2 calls per settings
 * render). On demand it fetches GET /api/sdlc-readiness, shows the
 * project type / gaps / missing secrets / operator checklist, and — when
 * the repo isn't ready and has capability gaps — a "Generate bootstrap
 * tickets" button that POSTs /api/bootstrap-plan (the BS3 generator).
 */

import { useState } from "react";

import { Badge, ButtonGhost, ButtonPrimary } from "@/components/ui";
import type { ApiBootstrapPlan, ApiSdlcReadiness } from "@/lib/api/client";

type Stage = "idle" | "loading" | "loaded" | "error";

async function _readError(res: Response): Promise<string> {
  const body = (await res.json().catch(() => ({}))) as {
    error?: string;
    detail?: string;
    message?: string;
  };
  return body.detail || body.message || body.error || `HTTP ${res.status}`;
}

export function SdlcReadinessCard({
  workspaceId,
  repoId,
  repoFullName,
}: {
  workspaceId: string;
  repoId: string;
  repoFullName: string;
}) {
  const [stage, setStage] = useState<Stage>("idle");
  const [data, setData] = useState<ApiSdlcReadiness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [plan, setPlan] = useState<ApiBootstrapPlan | null>(null);
  const [genError, setGenError] = useState<string | null>(null);

  async function check() {
    setStage("loading");
    setError(null);
    try {
      const res = await fetch(
        `/api/sdlc-readiness?ws=${encodeURIComponent(workspaceId)}&repo=${encodeURIComponent(repoId)}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(await _readError(res));
      setData((await res.json()) as ApiSdlcReadiness);
      setStage("loaded");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStage("error");
    }
  }

  async function generate() {
    setGenerating(true);
    setGenError(null);
    try {
      const res = await fetch("/api/bootstrap-plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ws: workspaceId, repo: repoId }),
      });
      if (!res.ok) throw new Error(await _readError(res));
      setPlan((await res.json()) as ApiBootstrapPlan);
    } catch (err) {
      setGenError(err instanceof Error ? err.message : String(err));
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-xs text-white/80">{repoFullName}</span>
        {stage === "idle" && (
          <ButtonGhost onClick={check}>Check readiness</ButtonGhost>
        )}
        {stage === "loading" && (
          <span className="animate-pulse text-xs text-white/50">Checking…</span>
        )}
        {stage === "loaded" && data && <StatusBadge data={data} />}
        {stage === "error" && (
          <ButtonGhost onClick={check}>Retry</ButtonGhost>
        )}
      </div>

      {stage === "error" && error && (
        <p className="mt-2 text-xs text-coral">{error}</p>
      )}

      {stage === "loaded" && data && (
        <Detail
          data={data}
          generating={generating}
          plan={plan}
          genError={genError}
          onGenerate={generate}
        />
      )}
    </div>
  );
}

function StatusBadge({ data }: { data: ApiSdlcReadiness }) {
  if (!data.has_blueprint) {
    return <Badge tone="neutral">{data.project_type ?? "unknown"}</Badge>;
  }
  if (data.ready) {
    return (
      <Badge tone="ok" dot>
        Ready
      </Badge>
    );
  }
  const n = data.gaps.length + data.missing_required_secrets.length;
  return (
    <Badge tone="warn" dot>
      {n} gap{n === 1 ? "" : "s"}
    </Badge>
  );
}

function Detail({
  data,
  generating,
  plan,
  genError,
  onGenerate,
}: {
  data: ApiSdlcReadiness;
  generating: boolean;
  plan: ApiBootstrapPlan | null;
  genError: string | null;
  onGenerate: () => void;
}) {
  // No blueprint (intel not harvested yet, or backend/library/unknown).
  if (!data.has_blueprint) {
    return (
      <p className="mt-2 text-xs text-white/55">
        {data.detail ?? "No bootstrap blueprint for this repo's project type."}
      </p>
    );
  }

  return (
    <div className="mt-3 space-y-3 text-xs">
      <div className="flex flex-wrap gap-2 text-white/55">
        <span>
          Type: <span className="text-white/80">{data.project_type}</span>
        </span>
        {data.delivery && (
          <span>
            Delivery: <span className="text-white/80">{data.delivery}</span>
          </span>
        )}
        {data.environments.length > 0 && (
          <span>
            Envs:{" "}
            <span className="text-white/80">
              {data.environments.join(", ")}
            </span>
          </span>
        )}
      </div>

      {data.ready ? (
        <p className="text-emerald-300">
          This repo meets its blueprint — ready for the SDLC pipeline.
        </p>
      ) : (
        <>
          {data.gaps.length > 0 && (
            <div>
              <p className="mb-1 text-white/45">Missing capabilities</p>
              <div className="flex flex-wrap gap-1.5">
                {data.gaps.map((g) => (
                  <Badge key={g} tone="warn">
                    {g}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {data.missing_required_secrets.length > 0 && (
            <div>
              <p className="mb-1 text-white/45">Missing required secrets</p>
              <div className="flex flex-wrap gap-1.5">
                {data.missing_required_secrets.map((s) => (
                  <Badge key={s} tone="err">
                    {s}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {data.external_checklist.length > 0 && (
            <details className="text-white/60">
              <summary className="cursor-pointer text-white/45">
                What you set up by hand ({data.external_checklist.length})
              </summary>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {data.external_checklist.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}

      {/* Action: generate the bootstrap epic when there are gaps to close. */}
      {!data.ready && data.gaps.length > 0 && !plan && (
        <div className="flex items-center gap-2">
          <ButtonPrimary onClick={generating ? undefined : onGenerate}>
            {generating ? "Generating…" : "Generate bootstrap tickets"}
          </ButtonPrimary>
          {genError && <span className="text-coral">{genError}</span>}
        </div>
      )}

      {plan && (
        <p className="text-emerald-300">
          Created {plan.tickets.length} ticket
          {plan.tickets.length === 1 ? "" : "s"} —{" "}
          <a
            href={plan.project_url}
            target="_blank"
            rel="noreferrer"
            className="underline hover:text-emerald-200"
          >
            view the bootstrap epic
          </a>
          .
        </p>
      )}
    </div>
  );
}
