"use client";

/**
 * WorkspaceDefaultsPanel — three rows the wizard now mandates before
 * any repo can be seeded:
 *
 *   1. Tracker — at least one workspace-level Integration row of kind
 *      linear/github/jira. Linear/Notion need OAuth; GitHub Issues
 *      rides on the GitHub App installation already wired up in
 *      step 1.
 *   2. Default agent profile — non-NULL ``Workspace.default_agent_
 *      profile``. Picker writes via PATCH /v1/workspaces/{ws}.
 *   3. Orchestrator — GitHub Actions only today; the wizard's first
 *      step already enforces an active GitHub App installation, so
 *      this row is informational, not actionable.
 *
 * The backend ``wizard_seed`` route 412s on the first two with
 * stable error codes (``workspace_default_agent_required`` /
 * ``workspace_tracker_required``); RepoCard surfaces those errors
 * inline alongside this panel's pre-emptive guidance.
 */

import { useState } from "react";
import Link from "next/link";

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

export interface WorkspaceDefaultsPanelInitial {
  workspaceId: string;
  /** ``null`` until the operator picks. Drives the warning state. */
  defaultAgentProfile: string | null;
  /**
   * Workspace-level trackers that are actually usable: linear/jira
   * with an OAuth/PAT token present, or kind=github (rides on the
   * GitHub App installation token).
   */
  trackerKinds: string[];
  /** GitHub App installed on this workspace? Drives the orchestrator
   *  row. We treat this as "installed=true" because the only way the
   *  wizard hits this step is after GitHub install succeeded. */
  githubAppInstalled: boolean;
}

export function WorkspaceDefaultsPanel({
  initial,
}: {
  initial: WorkspaceDefaultsPanelInitial;
}) {
  const [profile, setProfile] = useState<string | null>(
    initial.defaultAgentProfile,
  );
  const [trackerKinds] = useState<string[]>(initial.trackerKinds);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const trackerReady = trackerKinds.length > 0;
  const profileReady = Boolean(profile);
  const orchestratorReady = initial.githubAppInstalled;
  const allReady = trackerReady && profileReady && orchestratorReady;

  return (
    <section
      className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 shadow-card"
      data-testid="onboarding-confirm-workspace-defaults"
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-bold text-white">
            Workspace defaults
          </h2>
          <p className="mt-1 text-[11px] leading-snug text-white/55">
            Required before any repo can be seeded — applied to every
            seed PR and inherited by per-repo overrides.
          </p>
        </div>
        {allReady ? (
          <span className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-aqua">
            ready
          </span>
        ) : (
          <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-amber-200">
            action required
          </span>
        )}
      </header>

      <div className="mt-5 space-y-3">
        {/* Tracker row */}
        <Row
          label="Tracker"
          state={trackerReady ? "ok" : "missing"}
          summary={
            trackerReady
              ? trackerKinds.join(", ")
              : "No workspace tracker connected yet"
          }
        >
          {!trackerReady && (
            <Link
              href={`/onboarding?step=tracker&ws=${encodeURIComponent(
                initial.workspaceId,
              )}`}
              className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-3 py-1 text-[11px] font-semibold text-aqua transition hover:bg-aqua/[0.16]"
            >
              Connect tracker →
            </Link>
          )}
        </Row>

        {/* Default agent profile row */}
        <Row
          label="Default agent"
          state={profileReady ? "ok" : "missing"}
          summary={
            profileReady
              ? profileLabel(profile)
              : "Pick which coding agent runs per state"
          }
        >
          <select
            value={profile ?? ""}
            disabled={saving}
            onChange={async (e) => {
              const next = e.target.value || null;
              if (next === profile) return;
              if (!next) {
                // The PATCH ignores ``null`` so unsetting is a no-op
                // server-side; we don't surface "clear" in the UI.
                return;
              }
              setSaving(true);
              setSaveError(null);
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
                setProfile(next);
              } catch (err) {
                setSaveError(
                  err instanceof Error ? err.message : "save failed",
                );
              } finally {
                setSaving(false);
              }
            }}
            className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-white"
          >
            <option value="">— pick —</option>
            {AGENT_PROFILES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </Row>
        {saveError && (
          <p className="text-[11px] text-coral">
            Couldn&apos;t save default agent ({saveError}).
          </p>
        )}

        {/* Orchestrator row — GitHub Actions only today, satisfied by
            the GitHub App install in step 1. Shown for parity so an
            operator can see the full set of workspace-level invariants
            in one place. */}
        <Row
          label="Orchestrator"
          state={orchestratorReady ? "ok" : "missing"}
          summary={
            orchestratorReady
              ? "GitHub Actions (App installed)"
              : "GitHub App not installed — go back to step 1"
          }
        />
      </div>
    </section>
  );
}

function profileLabel(value: string | null): string {
  if (!value) return "";
  const match = AGENT_PROFILES.find((p) => p.value === value);
  return match?.label ?? value;
}

function Row({
  label,
  state,
  summary,
  children,
}: {
  label: string;
  state: "ok" | "missing";
  summary: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2">
      <div className="flex min-w-0 items-center gap-3">
        <span
          className={`rounded-full px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest ${
            state === "ok"
              ? "bg-aqua/15 text-aqua"
              : "bg-amber-500/15 text-amber-200"
          }`}
        >
          {state === "ok" ? "ok" : "missing"}
        </span>
        <div className="min-w-0">
          <div className="text-[12px] font-semibold text-white">{label}</div>
          <div className="truncate text-[11px] text-white/55">{summary}</div>
        </div>
      </div>
      {children && <div className="flex-shrink-0">{children}</div>}
    </div>
  );
}
