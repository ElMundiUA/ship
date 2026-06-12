/**
 * RolesStep — step 4 of 5.
 *
 * The Integrations step (3) is now plumbing-only: connect Linear,
 * Notion, Jira, etc. Roles is where the operator declares which of
 * the connected integrations plays which workspace-level role:
 *
 *   - Tracker — where Ship files tickets / clarifications.
 *   - Documentation — where curated knowledge lives (future
 *     workspace.docs_provider field; today shown read-only with a
 *     "coming soon" hint when both Notion AND Confluence are
 *     connected).
 *   - Default agent — which coding agent runs per state.
 *   - Code orchestrator — where pipelines execute. GitHub Actions
 *     only today; rendered read-only.
 *
 * Pre-fix the tracker picker was buried as a dropdown on the per-
 * repo card on the Confirm step, while step 3 was titled "Workspace
 * tracker" but accepted any integration. Roles makes the
 * "what plays which role" decision explicit + central.
 *
 * Plumbing:
 *   - Tracker dropdown PATCHes the workspace-level Integration row
 *     by reusing ``/api/onboard/tracker-install`` (kind=<chosen>).
 *     The server-side handler already exists; we just call it with
 *     a hidden form. For OAuth providers (linear/notion) the row
 *     must already exist with a token; the server enforces the
 *     gate from 2d8d843.
 *   - Default agent picker reuses ``/api/onboard/workspace-defaults``
 *     (the JSON endpoint introduced for the panel).
 */

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui";
import type { ApiAgentSecretStatus, TrackerKind } from "@/lib/api/client";

const AGENT_PROFILES: { value: string; label: string; hint: string }[] = [
  {
    value: "auto",
    label: "Auto",
    hint: "Ship picks per-state based on capability hints.",
  },
  {
    value: "main",
    label: "Main (default)",
    hint: "Strongest available coding agent for every state.",
  },
  {
    value: "cheaper",
    label: "Cheaper",
    hint: "Cost-tilted choice when capability allows.",
  },
  { value: "cursor_agent", label: "Cursor Cloud Agent", hint: "" },
  { value: "codex_cli", label: "OpenAI Codex CLI", hint: "" },
  { value: "ship_cloud_agent", label: "Ship Cloud Agent", hint: "" },
  { value: "local_cli", label: "Local CLI (self-hosted)", hint: "" },
];

const TRACKER_LABEL: Record<TrackerKind, string> = {
  linear: "Linear",
  github: "GitHub Issues",
  jira: "Jira",
};

export interface RolesStepInitial {
  workspaceId: string;
  repos: { id: string; full_name: string; private: boolean }[];
  /** Integration kinds with a usable token. ``github`` derives from
   *  the App install rather than an Integration row; the wizard
   *  pages already normalize that. */
  trackerCandidates: TrackerKind[];
  /**
   * Currently picked workspace-level tracker kind, if any. Read
   * from the most-recently-updated workspace ``Integration`` row at
   * page render time.
   */
  currentTrackerKind: TrackerKind | null;
  /** Current default_agent_profile. ``null`` until the operator
   *  picks one. */
  currentAgentProfile: string | null;
  /** Connected docs candidates — Confluence (via Atlassian native)
   *  + Notion. Today the wizard has nowhere to *persist* a docs-
   *  provider choice (no Workspace.docs_provider field), so this
   *  list renders read-only with a "coming soon" hint when more
   *  than one connection exists. Single connection → it's the docs
   *  provider implicitly. */
  docsCandidates: ("confluence" | "notion")[];
}

export function RolesStep({ initial }: { initial: RolesStepInitial }) {
  const [tracker, setTracker] = useState<TrackerKind | "">(
    initial.currentTrackerKind ?? "",
  );
  const [agent, setAgent] = useState<string>(
    initial.currentAgentProfile ?? "",
  );
  const [agentSaving, setAgentSaving] = useState(false);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [trackerSaving, setTrackerSaving] = useState(false);
  const [trackerError, setTrackerError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const [plannerRepoId, setPlannerRepoId] = useState(initial.repos[0]?.id ?? "");
  const [plannerSecrets, setPlannerSecrets] = useState<ApiAgentSecretStatus[]>([]);
  const [plannerDrafts, setPlannerDrafts] = useState<Record<string, string>>({});
  const [plannerLoading, setPlannerLoading] = useState(false);
  const [plannerSaving, setPlannerSaving] = useState(false);
  const [plannerError, setPlannerError] = useState<string | null>(null);
  const router = useRouter();

  const trackerOptions = initial.trackerCandidates;
  const trackerReady = tracker !== "";
  const agentReady = agent !== "";
  const allReady = trackerReady && agentReady;

  useEffect(() => {
    if (!plannerRepoId) return;
    let alive = true;
    setPlannerLoading(true);
    setPlannerError(null);
    fetch(
      `/api/onboard/agent-secrets?workspace_id=${encodeURIComponent(
        initial.workspaceId,
      )}&repo_id=${encodeURIComponent(
        plannerRepoId,
      )}&slugs=llm-openai,llm-anthropic,llm-gemini,llm-mistral`,
    )
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body: { check: { agents: ApiAgentSecretStatus[] } }) => {
        if (alive) setPlannerSecrets(body.check.agents);
      })
      .catch((err) => {
        if (alive) setPlannerError(err instanceof Error ? err.message : "load failed");
      })
      .finally(() => {
        if (alive) setPlannerLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [initial.workspaceId, plannerRepoId]);

  return (
    <section data-testid="onboarding-step-roles">
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 4 of 5 &middot; Roles
      </p>
      <h1 className="mt-2 font-display text-2xl font-bold leading-tight">
        Pick which tool plays which role.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        You connected the tools on the previous step. Now tell Ship
        which one is your tracker, which one holds docs, and which
        agent should do the heavy lifting. You can change any of
        these later from this step or from{" "}
        <Link href="/integrations" className="text-aqua underline">
          Integrations
        </Link>
        .
      </p>

      <div className="mt-7 rounded-2xl border border-white/[0.08] bg-white/[0.02]">
        <div className="px-4 pb-2 pt-3">
          <div className="text-[10px] font-bold uppercase tracking-wider text-white/40">
            Workspace roles
          </div>
        </div>
        <ul className="divide-y divide-white/[0.06]">
        {/* Tracker role */}
        <RoleRow
          label="Tracker"
          description="Where Ship files tickets, clarifications, and approvals."
          ready={trackerReady}
        >
          {trackerOptions.length === 0 ? (
            <p className="text-[11px] text-amber-200">
              No tracker-capable integration connected yet.{" "}
              <Link
                href={`/onboarding?step=tracker&ws=${encodeURIComponent(
                  initial.workspaceId,
                )}`}
                className="underline hover:text-amber-100"
              >
                Connect Linear / Jira / GitHub Issues →
              </Link>
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <div className="relative">
                <select
                  value={tracker}
                  disabled={trackerSaving}
                  onChange={async (e) => {
                    const next = e.target.value as TrackerKind | "";
                    if (!next || next === tracker) return;
                    setTrackerSaving(true);
                    setTrackerError(null);
                    // ``tracker-install`` is form-encoded by design, so
                    // we use a transient form to fire the request.
                    // Browser-side fetch with form-data also works and
                    // skips the page redirect.
                    try {
                      const fd = new FormData();
                      fd.set("ws", initial.workspaceId);
                      fd.set("kind", next);
                      const resp = await fetch(
                        "/api/onboard/tracker-install",
                        { method: "POST", body: fd, redirect: "manual" },
                      );
                      // The handler 303-redirects on success; with
                      // ``redirect: "manual"`` fetch returns an opaque
                      // response and ``resp.ok`` is false. Treat any
                      // non-error type as success — the action itself
                      // is fire-and-forget.
                      if (resp.type !== "opaqueredirect" && !resp.ok) {
                        throw new Error(`HTTP ${resp.status}`);
                      }
                      setTracker(next);
                    } catch (err) {
                      setTrackerError(
                        err instanceof Error ? err.message : "save failed",
                      );
                    } finally {
                      setTrackerSaving(false);
                    }
                  }}
                  className={
                    "appearance-none rounded-lg border px-3 py-2 pr-8 text-xs text-white bg-white/[0.04] " +
                    (submitted && !trackerReady
                      ? "border-coral/70 bg-coral/[0.06]"
                      : "border-white/[0.08]")
                  }
                >
                  <option value="">— pick a tracker —</option>
                  {trackerOptions.map((kind) => (
                    <option key={kind} value={kind}>
                      {TRACKER_LABEL[kind]}
                    </option>
                  ))}
                </select>
                <span className="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-white/45">
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </span>
              </div>
              {trackerSaving && (
                <span className="text-[11px] text-white/55">Saving…</span>
              )}
            </div>
          )}
          {trackerError && (
            <p className="mt-2 text-[11px] text-coral">
              Couldn&apos;t save tracker ({trackerError}).
            </p>
          )}
        </RoleRow>

        {/* Documentation role — read-only for now */}
        <RoleRow
          label="Documentation"
          description="Where curated knowledge buckets live."
          ready={initial.docsCandidates.length > 0}
          hideBadge
        >
          {initial.docsCandidates.length === 0 ? (
            <p className="text-[11px] text-white/55">
              No docs source connected. Notion or Confluence will land
              here once connected.
            </p>
          ) : (
            <div className="space-y-1">
              <p className="text-[11px] text-white/65">
                Currently using{" "}
                <strong className="text-white">
                  {initial.docsCandidates
                    .map((d) => (d === "notion" ? "Notion" : "Confluence"))
                    .join(" + ")}
                </strong>
                .
              </p>
              {initial.docsCandidates.length > 1 && (
                <p className="text-[10px] text-white/45">
                  Multiple docs sources connected — the picker for
                  primary docs source is in the next iteration. Both
                  feed the curated knowledge index for now.
                </p>
              )}
            </div>
          )}
        </RoleRow>

        {/* Default agent */}
        <RoleRow
          label="Default agent"
          description="Which coding agent gets called when a process state doesn't override."
          ready={agentReady}
        >
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <select
                value={agent}
                disabled={agentSaving}
                onChange={async (e) => {
                  const next = e.target.value;
                  if (!next || next === agent) return;
                  setAgentSaving(true);
                  setAgentError(null);
                  try {
                    const resp = await fetch(
                      "/api/onboard/workspace-defaults",
                      {
                        method: "PATCH",
                        headers: { "content-type": "application/json" },
                        body: JSON.stringify({
                          workspace_id: initial.workspaceId,
                          default_agent_profile: next,
                        }),
                      },
                    );
                    if (!resp.ok) {
                      const payload = await resp.json().catch(() => ({}));
                      throw new Error(
                        typeof payload?.error === "string"
                          ? payload.error
                          : `HTTP ${resp.status}`,
                      );
                    }
                    setAgent(next);
                  } catch (err) {
                    setAgentError(
                      err instanceof Error ? err.message : "save failed",
                    );
                  } finally {
                    setAgentSaving(false);
                  }
                }}
                className={
                  "appearance-none rounded-lg border px-3 py-2 pr-8 text-xs text-white bg-white/[0.04] " +
                  (submitted && !agentReady
                    ? "border-coral/70 bg-coral/[0.06]"
                    : "border-white/[0.08]")
                }
              >
                <option value="">— pick a default agent —</option>
                {AGENT_PROFILES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
              <span className="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-white/45">
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </span>
            </div>
            {agentSaving && (
              <span className="text-[11px] text-white/55">Saving…</span>
            )}
          </div>
          {agentError && (
            <p className="mt-2 text-[11px] text-coral">
              Couldn&apos;t save default agent ({agentError}).
            </p>
          )}
        </RoleRow>

        {/* Orchestrator — read-only */}
        <RoleRow
          label="Code orchestrator"
          description="Where pipelines execute. Only GitHub Actions today; GitLab CI / Jenkins are on the roadmap."
          ready={true}
        >
          <p className="text-[11px] text-white/65">
            <strong className="text-white">GitHub Actions</strong> —
            tied to the App you installed in step 1. No action needed.
          </p>
        </RoleRow>

        <RoleRow
          label="LLM API keys"
          description="Optional provider keys for Ship workflows in this repo. Today they power deployment planning; later they can power generated app AI features."
          ready={plannerSecrets.some((s) => s.present)}
          hideBadge
        >
          {initial.repos.length === 0 ? (
            <p className="text-[11px] text-white/55">
              Pick a repo first; planner keys are stored as per-repo GitHub
              Actions secrets.
            </p>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <select
                  value={plannerRepoId}
                  onChange={(e) => {
                    setPlannerRepoId(e.target.value);
                    setPlannerDrafts({});
                  }}
                  className="rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-xs text-white"
                >
                  {initial.repos.map((repo) => (
                    <option key={repo.id} value={repo.id}>
                      {repo.full_name}
                      {repo.private ? " (private)" : ""}
                    </option>
                  ))}
                </select>
                {plannerLoading && (
                  <span className="text-[11px] text-white/55">Checking…</span>
                )}
              </div>

              {plannerSecrets.map((secret) => (
                <div
                  key={secret.slug}
                  className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-white">
                          {secret.label}
                        </span>
                        <span
                          className={
                            secret.present
                              ? "rounded bg-aqua/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-aqua"
                              : "rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-amber-200"
                          }
                        >
                          {secret.present ? "present" : "optional"}
                        </span>
                      </div>
                      {secret.secret_name && (
                        <code className="mt-1 inline-block rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-white/60">
                          secrets.{secret.secret_name}
                        </code>
                      )}
                    </div>
                    {secret.vendor_url && (
                      <a
                        href={secret.vendor_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[11px] text-aqua/80 underline-offset-2 hover:text-aqua hover:underline"
                      >
                        Get key →
                      </a>
                    )}
                  </div>
                  {!secret.present && (
                    <input
                      type="password"
                      placeholder={`${secret.secret_name} — paste plaintext key`}
                      value={plannerDrafts[secret.slug] ?? ""}
                      onChange={(e) =>
                        setPlannerDrafts((d) => ({
                          ...d,
                          [secret.slug]: e.target.value,
                        }))
                      }
                      className="mt-2 w-full rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 font-mono text-xs text-white placeholder-white/35"
                    />
                  )}
                </div>
              ))}

              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  disabled={
                    plannerSaving ||
                    !Object.values(plannerDrafts).some((v) => v.trim().length > 0)
                  }
                  onClick={async () => {
                    setPlannerSaving(true);
                    setPlannerError(null);
                    const secrets = Object.entries(plannerDrafts)
                      .filter(([, plaintext]) => plaintext.trim().length > 0)
                      .map(([slug, plaintext]) => ({
                        slug,
                        plaintext: plaintext.trim(),
                      }));
                    try {
                      const resp = await fetch("/api/onboard/agent-secrets", {
                        method: "POST",
                        headers: { "content-type": "application/json" },
                        body: JSON.stringify({
                          workspace_id: initial.workspaceId,
                          repo_id: plannerRepoId,
                          secrets,
                        }),
                      });
                      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                      setPlannerDrafts({});
                      const checkResp = await fetch(
                        `/api/onboard/agent-secrets?workspace_id=${encodeURIComponent(
                          initial.workspaceId,
                        )}&repo_id=${encodeURIComponent(
                          plannerRepoId,
                        )}&slugs=llm-openai,llm-anthropic,llm-gemini,llm-mistral`,
                      );
                      if (checkResp.ok) {
                        const body = (await checkResp.json()) as {
                          check: { agents: ApiAgentSecretStatus[] };
                        };
                        setPlannerSecrets(body.check.agents);
                      }
                    } catch (err) {
                      setPlannerError(
                        err instanceof Error ? err.message : "save failed",
                      );
                    } finally {
                      setPlannerSaving(false);
                    }
                  }}
                  className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-3 py-1 text-[11px] font-semibold text-aqua transition hover:bg-aqua/[0.16] disabled:opacity-40"
                >
                  {plannerSaving ? "Saving…" : "Save planner keys"}
                </button>
                {plannerError && (
                  <span className="text-[11px] text-coral">
                    Planner keys error: {plannerError}
                  </span>
                )}
              </div>
            </div>
          )}
        </RoleRow>
        </ul>
      </div>

      <div className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.08] pt-5">
        <span className="text-[11px] text-white/45">
          {allReady
            ? "All set. Continue to review repos."
            : "Pick a tracker and a default agent before continuing — Ship needs both before opening seed PRs."}
        </span>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <Link
            href={`/onboarding?step=tracker&ws=${encodeURIComponent(
              initial.workspaceId,
            )}`}
            className="text-white/55 hover:text-white"
          >
            ← Integrations
          </Link>
          <button
            type="button"
            data-testid="onboarding-roles-continue"
            disabled={continuing}
            onClick={async () => {
              if (!allReady) {
                setSubmitted(true);
                return;
              }
              setContinuing(true);
              // Ensure the workspace-level tracker Integration row exists
              // before navigating. tracker-install with kind=github creates
              // the row if it's missing — this covers the case where the
              // user arrived with github pre-selected but never changed the
              // dropdown (so the onChange fetch never fired).
              if (tracker) {
                try {
                  const fd = new FormData();
                  fd.set("ws", initial.workspaceId);
                  fd.set("kind", tracker);
                  await fetch("/api/onboard/tracker-install", {
                    method: "POST",
                    body: fd,
                    redirect: "manual",
                  });
                } catch {
                  // best-effort
                }
              }
              router.push(
                `/onboarding?step=confirm&ws=${encodeURIComponent(initial.workspaceId)}`,
              );
            }}
            className={
              "rounded-md px-3 py-1.5 text-sm font-semibold transition " +
              (continuing
                ? "cursor-not-allowed bg-white/[0.04] text-white/30"
                : allReady
                  ? "bg-aqua/15 text-aqua hover:bg-aqua/25"
                  : "bg-white/[0.02] text-white/40 hover:bg-white/[0.05] hover:text-white/60")
            }
          >
            {continuing ? (
              <span className="inline-flex items-center gap-2">
                <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"/>
                </svg>
                Saving…
              </span>
            ) : (
              "Continue →"
            )}
          </button>
        </div>
      </div>
    </section>
  );
}

function RoleRow({
  label,
  description,
  ready,
  hideBadge,
  children,
}: {
  label: string;
  description: string;
  ready: boolean;
  hideBadge?: boolean;
  children: React.ReactNode;
}) {
  return (
    <li className="px-4 py-3">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-white">{label}</span>
            {!hideBadge && (
              <Badge tone={ready ? "ok" : "warn"}>
                {ready ? "ready" : "needed"}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 text-[11px] text-white/60">{description}</p>
        </div>
      </header>
      <div className="mt-2">{children}</div>
    </li>
  );
}
