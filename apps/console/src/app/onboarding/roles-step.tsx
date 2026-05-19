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
import { useState } from "react";

import type { TrackerKind } from "@/lib/api/client";

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

  const trackerOptions = initial.trackerCandidates;
  const trackerReady = tracker !== "";
  const agentReady = agent !== "";
  const allReady = trackerReady && agentReady;

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

      <div className="mt-7 space-y-4">
        {/* Tracker role */}
        <RoleCard
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
                className="rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-xs text-white"
              >
                <option value="">— pick a tracker —</option>
                {trackerOptions.map((kind) => (
                  <option key={kind} value={kind}>
                    {TRACKER_LABEL[kind]}
                  </option>
                ))}
              </select>
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
        </RoleCard>

        {/* Documentation role — read-only for now */}
        <RoleCard
          label="Documentation"
          description="Where curated knowledge buckets live."
          ready={initial.docsCandidates.length > 0}
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
        </RoleCard>

        {/* Default agent */}
        <RoleCard
          label="Default agent"
          description="Which coding agent gets called when a process state doesn't override."
          ready={agentReady}
        >
          <div className="flex flex-wrap items-center gap-3">
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
              className="rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-xs text-white"
            >
              <option value="">— pick a default agent —</option>
              {AGENT_PROFILES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
            {agentSaving && (
              <span className="text-[11px] text-white/55">Saving…</span>
            )}
          </div>
          {agentError && (
            <p className="mt-2 text-[11px] text-coral">
              Couldn&apos;t save default agent ({agentError}).
            </p>
          )}
        </RoleCard>

        {/* Orchestrator — read-only */}
        <RoleCard
          label="Code orchestrator"
          description="Where pipelines execute. Only GitHub Actions today; GitLab CI / Jenkins are on the roadmap."
          ready={true}
        >
          <p className="text-[11px] text-white/65">
            <strong className="text-white">GitHub Actions</strong> —
            tied to the App you installed in step 1. No action needed.
          </p>
        </RoleCard>
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
          <Link
            href={`/onboarding?step=confirm&ws=${encodeURIComponent(
              initial.workspaceId,
            )}`}
            data-testid="onboarding-roles-continue"
            className={`rounded-full px-4 py-2 text-xs font-bold transition ${
              allReady
                ? "border border-aqua/40 bg-aqua/[0.08] text-aqua hover:bg-aqua/[0.16]"
                : "border border-white/[0.08] bg-white/[0.02] text-white/40 pointer-events-none"
            }`}
          >
            Continue →
          </Link>
        </div>
      </div>
    </section>
  );
}

function RoleCard({
  label,
  description,
  ready,
  children,
}: {
  label: string;
  description: string;
  ready: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5 shadow-card">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-base font-bold text-white">
            {label}
          </h2>
          <p className="mt-0.5 text-[11px] text-white/55">{description}</p>
        </div>
        <span
          className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest ${
            ready
              ? "border-aqua/40 bg-aqua/[0.08] text-aqua"
              : "border-amber-500/40 bg-amber-500/10 text-amber-200"
          }`}
        >
          {ready ? "ready" : "needed"}
        </span>
      </header>
      <div className="mt-3">{children}</div>
    </div>
  );
}
