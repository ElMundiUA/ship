"use client";

/**
 * RepoCard — per-repo configuration card for the wizard v2 configure
 * step. Handles:
 *
 *   - Preset picker (radio; persisted via ``PATCH /repos/{id}``).
 *   - Tracker binding (kind select + optional config; persisted via
 *     ``POST /api/onboard/tracker-bind``).
 *   - Agent secrets (catalog check + inline plaintext inputs for
 *     anything missing; pushed via ``POST /api/onboard/agent-secrets``).
 *   - "Open seed PR" button (calls ``POST /api/onboard/wizard-seed``;
 *     disabled until the gate passes).
 *
 * Once a seed PR has been opened the card collapses to a status row
 * carrying the PR link and a ``Reseed`` escape hatch (rotates the
 * run token and opens a fresh PR).
 *
 * State model
 * -----------
 *
 * Backend is the source of truth. On mount the card renders the
 * server-provided snapshot and then only mutates through the route
 * handlers. Each mutation returns the fresh row (or an error code)
 * and the card patches its local state from the response. We keep
 * the seed PR result purely in local state — the wizard surfaces it
 * until the user merges the PR, at which point the dashboard banner
 * (iter 8) takes over.
 */

import { useMemo, useState } from "react";

import type {
  ApiAgentSecretStatus,
  ApiTrackerBinding,
  ApiWizardSeedResult,
  TrackerKind,
} from "@/lib/api/client";

import { PRESET_META, type PresetId } from "./presets";

export interface RepoCardInitial {
  repo: {
    id: string;
    full_name: string;
    preset: PresetId | null;
    default_branch: string;
  };
  tracker: ApiTrackerBinding;
  agents: ApiAgentSecretStatus[];
  /** Pre-populated when the wizard was reloaded after a prior seed. */
  last_seed?: {
    pr_url: string;
    pr_number: number;
  } | null;
  /**
   * Non-fatal per-probe failures from the server render. Each entry is a
   * short human-readable message. The card stays interactive — missing
   * tracker data is treated as "none", missing secrets as "unknown" —
   * and surfaces these hints so the operator can reason about what's
   * actually wired vs. probed.
   *
   * Why the fields aren't just absent: users reported the whole step 4
   * bombing with "Couldn't load your activated repos" when a single
   * probe 500'd (App missing Secrets permission is the common cause).
   * Carrying a soft error lets us render the rest and point the user
   * at the actual fix instead of a cryptic banner.
   */
  probe_errors?: {
    tracker?: string;
    agents?: string;
  };
}

export function RepoCard({
  workspaceId,
  initial,
}: {
  workspaceId: string;
  initial: RepoCardInitial;
}) {
  // ── Local state ───────────────────────────────────────────────
  const [preset, setPreset] = useState<PresetId | null>(initial.repo.preset);
  const [presetSaving, setPresetSaving] = useState(false);
  const [presetError, setPresetError] = useState<string | null>(null);

  const [tracker, setTracker] = useState<ApiTrackerBinding>(initial.tracker);
  const [trackerSaving, setTrackerSaving] = useState(false);
  const [trackerError, setTrackerError] = useState<string | null>(null);
  const [trackerDraft, setTrackerDraft] = useState<{
    kind: TrackerKind | "";
    team: string;
    project: string;
  }>(() => ({
    kind: (tracker.kind as TrackerKind | null) ?? "",
    team: pickConfig(tracker.config, "team_id") ?? "",
    project: pickConfig(tracker.config, "project") ?? "",
  }));

  const [agents, setAgents] = useState<ApiAgentSecretStatus[]>(initial.agents);
  const [secretDrafts, setSecretDrafts] = useState<Record<string, string>>({});
  const [secretsSaving, setSecretsSaving] = useState(false);
  const [secretsError, setSecretsError] = useState<string | null>(null);
  const [secretsFailed, setSecretsFailed] = useState<string[]>([]);

  const [seedResult, setSeedResult] = useState<ApiWizardSeedResult | null>(
    initial.last_seed
      ? {
          pr_url: initial.last_seed.pr_url,
          pr_number: initial.last_seed.pr_number,
          branch: "",
          files: [],
          presets: initial.repo.preset ? [initial.repo.preset] : [],
          knowledge_slugs: [],
          tracker_kind: tracker.kind,
          run_token_prefix: null,
          run_token_rotated: false,
        }
      : null,
  );
  const [seedSaving, setSeedSaving] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);

  // ── Derived ───────────────────────────────────────────────────
  const missingRequiredSecrets = useMemo(
    () => agents.filter((a) => a.required && !a.present),
    [agents],
  );

  const readyToSeed = Boolean(
    preset &&
      missingRequiredSecrets.length === 0 &&
      !seedSaving &&
      !presetSaving &&
      !secretsSaving &&
      !trackerSaving,
  );

  // Mint-state pill: collapsed card once the PR is open.
  if (seedResult) {
    return (
      <SeededRow
        workspaceId={workspaceId}
        repoFullName={initial.repo.full_name}
        repoId={initial.repo.id}
        seed={seedResult}
        onReseed={async () => {
          setSeedSaving(true);
          setSeedError(null);
          const resp = await fetch("/api/onboard/wizard-seed", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              workspace_id: workspaceId,
              repo_id: initial.repo.id,
              presets: preset ? [preset] : undefined,
              tracker_kind: tracker.kind ?? null,
              rotate_run_token: true,
            }),
          });
          setSeedSaving(false);
          if (!resp.ok) {
            const payload = await resp.json().catch(() => ({}));
            setSeedError(payload?.error ?? "unknown");
            return;
          }
          const body = (await resp.json()) as { result: ApiWizardSeedResult };
          setSeedResult(body.result);
        }}
        error={seedError}
        saving={seedSaving}
      />
    );
  }

  return (
    <section
      className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 shadow-card"
      data-testid={`wizard-repo-card-${initial.repo.full_name}`}
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-lg font-bold text-white">
            {initial.repo.full_name}
          </h3>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-white/45">
            <span>{initial.repo.default_branch}</span>
            {tracker.kind && tracker.source === "workspace" && (
              <span className="rounded bg-white/5 px-1.5 py-0.5">
                inherits {tracker.kind}
              </span>
            )}
            {tracker.kind && tracker.source === "repo" && (
              <span className="rounded bg-aqua/15 px-1.5 py-0.5 text-aqua">
                {tracker.kind}
              </span>
            )}
            {!tracker.kind && (
              <span className="rounded bg-white/5 px-1.5 py-0.5 text-white/45">
                no tracker
              </span>
            )}
          </div>
        </div>
        <PresetBadge preset={preset} />
      </header>

      {/* ── Preset ────────────────────────────────────────────── */}
      <fieldset className="mt-5">
        <legend className="text-[11px] font-bold uppercase tracking-widest text-white/55">
          Preset
        </legend>
        <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
          {(Object.keys(PRESET_META) as PresetId[]).map((pid) => {
            const meta = PRESET_META[pid];
            const checked = preset === pid;
            return (
              <label
                key={pid}
                className={
                  "flex cursor-pointer items-start gap-2 rounded-xl border p-3 text-left transition " +
                  (checked
                    ? "border-aqua/60 bg-aqua/[0.08]"
                    : "border-white/5 bg-white/[0.02] hover:border-aqua/30")
                }
              >
                <input
                  type="radio"
                  className="mt-1"
                  name={`preset-${initial.repo.id}`}
                  checked={checked}
                  onChange={async () => {
                    setPreset(pid);
                    setPresetError(null);
                    setPresetSaving(true);
                    try {
                      const resp = await fetch(
                        `/api/repos/${encodeURIComponent(initial.repo.id)}/preset`,
                        {
                          method: "POST",
                          headers: { "content-type": "application/json" },
                          body: JSON.stringify({
                            workspace_id: workspaceId,
                            preset: pid,
                          }),
                        },
                      );
                      if (!resp.ok) {
                        const payload = await resp.json().catch(() => ({}));
                        setPresetError(payload?.error ?? "unknown");
                      }
                    } catch {
                      setPresetError("unknown");
                    } finally {
                      setPresetSaving(false);
                    }
                  }}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-white">{meta.name}</span>
                    <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-white/45">
                      {pid}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[11px] leading-snug text-white/60">
                    {meta.blurb}
                  </p>
                </div>
              </label>
            );
          })}
        </div>
        {presetError && (
          <p className="mt-2 text-[11px] text-coral">
            Couldn&apos;t save preset ({presetError}). Try again.
          </p>
        )}
      </fieldset>

      {/* ── Tracker binding ────────────────────────────────────── */}
      <fieldset className="mt-5">
        <legend className="text-[11px] font-bold uppercase tracking-widest text-white/55">
          Tracker
        </legend>
        {initial.probe_errors?.tracker && (
          <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-snug text-amber-200">
            Couldn&apos;t read the current tracker binding:{" "}
            <code className="font-mono">{initial.probe_errors.tracker}</code>.
            Pick a tracker below to overwrite whatever&apos;s stored.
          </p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {(["linear", "github", "jira"] as TrackerKind[]).map((k) => {
            const selected = trackerDraft.kind === k;
            return (
              <button
                key={k}
                type="button"
                onClick={() =>
                  setTrackerDraft((d) => ({
                    ...d,
                    kind: selected ? "" : k,
                  }))
                }
                className={
                  "rounded-full border px-3 py-1 text-xs font-medium transition " +
                  (selected
                    ? "border-aqua/60 bg-aqua/[0.1] text-aqua"
                    : "border-white/10 bg-white/[0.04] text-white/70 hover:border-aqua/30")
                }
              >
                {k}
              </button>
            );
          })}
          {tracker.workspace_default_kind &&
            tracker.source !== "repo" &&
            trackerDraft.kind === "" && (
              <span className="text-[11px] text-white/40">
                inheriting workspace default{" "}
                <code className="rounded bg-white/5 px-1">
                  {tracker.workspace_default_kind}
                </code>
              </span>
            )}
        </div>

        {trackerDraft.kind === "linear" && (
          <input
            type="text"
            placeholder="Linear team key (optional, e.g. ENG)"
            value={trackerDraft.team}
            onChange={(e) =>
              setTrackerDraft((d) => ({ ...d, team: e.target.value }))
            }
            className="mt-2 w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-white placeholder-white/35"
          />
        )}
        {trackerDraft.kind === "jira" && (
          <input
            type="text"
            placeholder="Jira project key (e.g. WIDG)"
            value={trackerDraft.project}
            onChange={(e) =>
              setTrackerDraft((d) => ({ ...d, project: e.target.value }))
            }
            className="mt-2 w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-white placeholder-white/35"
          />
        )}

        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            disabled={trackerSaving || trackerDraft.kind === ""}
            onClick={async () => {
              if (trackerDraft.kind === "") return;
              setTrackerSaving(true);
              setTrackerError(null);
              const config: Record<string, string> = {};
              if (trackerDraft.kind === "linear" && trackerDraft.team.trim())
                config.team_id = trackerDraft.team.trim();
              if (trackerDraft.kind === "jira" && trackerDraft.project.trim())
                config.project = trackerDraft.project.trim();
              const resp = await fetch("/api/onboard/tracker-bind", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({
                  workspace_id: workspaceId,
                  repo_id: initial.repo.id,
                  kind: trackerDraft.kind,
                  config,
                }),
              });
              setTrackerSaving(false);
              if (!resp.ok) {
                const payload = await resp.json().catch(() => ({}));
                setTrackerError(payload?.error ?? "unknown");
                return;
              }
              const body = (await resp.json()) as {
                binding: ApiTrackerBinding;
              };
              setTracker(body.binding);
            }}
            className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-3 py-1 text-[11px] font-semibold text-aqua transition hover:bg-aqua/[0.16] disabled:opacity-40"
          >
            {trackerSaving ? "Saving..." : "Save tracker"}
          </button>
          {tracker.source === "repo" && (
            <button
              type="button"
              onClick={async () => {
                setTrackerSaving(true);
                const resp = await fetch(
                  `/api/onboard/tracker-bind?workspace_id=${encodeURIComponent(
                    workspaceId,
                  )}&repo_id=${encodeURIComponent(initial.repo.id)}`,
                  { method: "DELETE" },
                );
                setTrackerSaving(false);
                if (resp.ok) {
                  // Clear per-repo row; UI will fall back to workspace default.
                  setTracker({
                    ...tracker,
                    kind: tracker.workspace_default_kind,
                    source: tracker.workspace_default_kind ? "workspace" : "none",
                  });
                  setTrackerDraft({
                    kind: (tracker.workspace_default_kind as TrackerKind | null) ?? "",
                    team: "",
                    project: "",
                  });
                }
              }}
              className="text-[11px] text-white/55 hover:text-white"
            >
              Unbind
            </button>
          )}
          {trackerError && (
            <span className="text-[11px] text-coral">
              Couldn&apos;t save ({trackerError}).
            </span>
          )}
        </div>
      </fieldset>

      {/* ── Agent secrets ──────────────────────────────────────── */}
      <fieldset className="mt-5">
        <legend className="text-[11px] font-bold uppercase tracking-widest text-white/55">
          Agents
        </legend>
        <p className="mt-1 text-[11px] text-white/55">
          Ship reads these from the repo&apos;s GitHub Actions secrets. Keys
          you paste here go straight to GitHub and are never stored on Ship.
        </p>
        {initial.probe_errors?.agents && (
          <AgentsProbeError message={initial.probe_errors.agents} />
        )}
        <div className="mt-2 space-y-2">
          {agents.map((a) => {
            const hasDraft = secretDrafts[a.slug]?.length > 0;
            const failed = secretsFailed.includes(a.slug);
            return (
              <div
                key={a.slug}
                className="rounded-xl border border-white/10 bg-white/[0.02] p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-white">{a.label}</span>
                      {a.required ? (
                        a.present ? (
                          <span className="rounded bg-aqua/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-aqua">
                            present
                          </span>
                        ) : (
                          <span className="rounded bg-coral/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-coral">
                            missing
                          </span>
                        )
                      ) : (
                        <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-white/55">
                          no key needed
                        </span>
                      )}
                      {failed && (
                        <span className="rounded bg-coral/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-coral">
                          push failed
                        </span>
                      )}
                    </div>
                    {a.description && (
                      <p className="mt-0.5 text-[11px] leading-snug text-white/55">
                        {a.description}
                      </p>
                    )}
                    {a.secret_name && (
                      <code className="mt-1 inline-block rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-white/60">
                        secrets.{a.secret_name}
                      </code>
                    )}
                  </div>
                  {a.vendor_url && (
                    <a
                      href={a.vendor_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[11px] text-aqua/80 underline-offset-2 hover:text-aqua hover:underline"
                    >
                      Get key →
                    </a>
                  )}
                </div>
                {a.required && !a.present && (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <input
                      type="password"
                      placeholder={`${a.secret_name} — paste plaintext key`}
                      value={secretDrafts[a.slug] ?? ""}
                      onChange={(e) =>
                        setSecretDrafts((d) => ({
                          ...d,
                          [a.slug]: e.target.value,
                        }))
                      }
                      className="min-w-[240px] flex-1 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 font-mono text-xs text-white placeholder-white/35"
                    />
                    {hasDraft && (
                      <span className="text-[11px] text-white/45">
                        (will push on &ldquo;Save secrets&rdquo;)
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={
              secretsSaving ||
              !Object.values(secretDrafts).some((v) => v.trim().length > 0)
            }
            onClick={async () => {
              setSecretsSaving(true);
              setSecretsError(null);
              setSecretsFailed([]);
              const payload = Object.entries(secretDrafts)
                .filter(([, v]) => v.trim().length > 0)
                .map(([slug, plaintext]) => ({
                  slug,
                  plaintext: plaintext.trim(),
                }));
              const resp = await fetch("/api/onboard/agent-secrets", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({
                  workspace_id: workspaceId,
                  repo_id: initial.repo.id,
                  secrets: payload,
                }),
              });
              setSecretsSaving(false);
              if (!resp.ok) {
                const p = await resp.json().catch(() => ({}));
                setSecretsError(p?.error ?? "unknown");
                return;
              }
              const body = (await resp.json()) as {
                result: { pushed: string[]; failed: { slug: string }[] };
              };
              // Refresh the catalog so present-flags flip live.
              try {
                const checkResp = await fetch(
                  `/api/onboard/agent-secrets?workspace_id=${encodeURIComponent(
                    workspaceId,
                  )}&repo_id=${encodeURIComponent(initial.repo.id)}`,
                );
                if (checkResp.ok) {
                  const c = (await checkResp.json()) as {
                    check: { agents: ApiAgentSecretStatus[] };
                  };
                  setAgents(c.check.agents);
                }
              } catch {
                // Non-fatal: stale ``present`` flag, user can re-check.
              }
              // Clear drafts for the slugs we just pushed successfully,
              // keep the ones that failed so the user can retry.
              const pushed = new Set(body.result.pushed);
              setSecretDrafts((d) => {
                const next = { ...d };
                for (const slug of Object.keys(next)) {
                  if (pushed.has(slug)) delete next[slug];
                }
                return next;
              });
              setSecretsFailed(body.result.failed.map((f) => f.slug));
            }}
            className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-3 py-1 text-[11px] font-semibold text-aqua transition hover:bg-aqua/[0.16] disabled:opacity-40"
          >
            {secretsSaving ? "Pushing..." : "Save secrets"}
          </button>
          {secretsError && (
            <span className="text-[11px] text-coral">
              Couldn&apos;t push ({secretsError}).
            </span>
          )}
        </div>
      </fieldset>

      {/* ── Seed button ────────────────────────────────────────── */}
      <footer className="mt-6 border-t border-white/10 pt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0 text-[11px] leading-snug text-white/55">
            {readyToSeed ? (
              <>
                Ready to seed. Opens one PR with workflows, config, knowledge
                starters and the tracker FSM.
              </>
            ) : (
              <>
                <strong className="text-white/80">Not ready.</strong>{" "}
                {missingBlockers(preset, missingRequiredSecrets)}
              </>
            )}
          </div>
          <button
            type="button"
            data-testid={`wizard-seed-${initial.repo.full_name}`}
            disabled={!readyToSeed}
            onClick={async () => {
              setSeedSaving(true);
              setSeedError(null);
              const resp = await fetch("/api/onboard/wizard-seed", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({
                  workspace_id: workspaceId,
                  repo_id: initial.repo.id,
                  presets: preset ? [preset] : undefined,
                  tracker_kind: tracker.kind ?? null,
                }),
              });
              setSeedSaving(false);
              if (!resp.ok) {
                const p = await resp.json().catch(() => ({}));
                setSeedError(p?.error ?? "unknown");
                return;
              }
              const body = (await resp.json()) as {
                result: ApiWizardSeedResult;
              };
              setSeedResult(body.result);
            }}
            className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2 text-xs font-bold text-ink shadow-glow transition hover:brightness-110 disabled:opacity-40"
          >
            {seedSaving ? "Opening PR..." : "Open seed PR →"}
          </button>
        </div>
        {seedError && (
          <p className="mt-2 text-[11px] text-coral">
            Seed PR didn&apos;t open ({seedError}). Try again once the
            blocker clears.
          </p>
        )}
      </footer>
    </section>
  );
}

function SeededRow({
  repoFullName,
  seed,
  onReseed,
  error,
  saving,
}: {
  workspaceId: string;
  repoId: string;
  repoFullName: string;
  seed: ApiWizardSeedResult;
  onReseed: () => Promise<void>;
  error: string | null;
  saving: boolean;
}) {
  return (
    <section
      className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-aqua/30 bg-aqua/[0.05] p-4"
      data-testid={`wizard-repo-card-${repoFullName}-seeded`}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="grid h-5 w-5 place-items-center rounded-full bg-aqua/20 text-[10px] text-aqua">
            ✓
          </span>
          <span className="font-semibold text-white">{repoFullName}</span>
          <span className="rounded bg-aqua/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-aqua">
            seeded
          </span>
        </div>
        <div className="mt-1 text-[11px] text-white/70">
          PR opened —{" "}
          <a
            href={seed.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-aqua underline-offset-2 hover:underline"
          >
            #{seed.pr_number}
          </a>{" "}
          · merge to activate the lanes.
        </div>
        {error && <p className="mt-1 text-[11px] text-coral">{error}</p>}
      </div>
      <button
        type="button"
        onClick={onReseed}
        disabled={saving}
        className="text-[11px] text-white/55 hover:text-white disabled:opacity-40"
      >
        {saving ? "Reseeding..." : "Reseed (rotate token)"}
      </button>
    </section>
  );
}

function PresetBadge({ preset }: { preset: PresetId | null }) {
  if (!preset) {
    return (
      <span className="rounded-full border border-coral/40 bg-coral/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-coral">
        no preset
      </span>
    );
  }
  return (
    <span className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-aqua">
      {preset}
    </span>
  );
}

function pickConfig(config: Record<string, unknown>, key: string): string | undefined {
  const v = config?.[key];
  return typeof v === "string" ? v : undefined;
}

/**
 * Render the amber banner shown when the agent-secrets probe failed.
 *
 * Backend 412 payloads carry a ``code`` field that narrows the cause
 * (missing_secrets_permission / installation_token_rejected /
 * actions_disabled / github_upstream_error). We pluck it out of the
 * message string (which ``formatProbeError`` built as ``HTTP 412 — {...}``)
 * and pick copy accordingly. For anything else (generic 500, network
 * blip, etc.) we fall through to the legacy "most often this means"
 * copy — it's a reasonable default since that's the #1 cause in the
 * wild.
 */
function AgentsProbeError({ message }: { message: string }) {
  const code = extractProbeCode(message);
  if (code === "missing_secrets_permission") {
    return (
      <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-snug text-amber-200">
        The Ship GitHub App is missing the{" "}
        <strong>Secrets: read &amp; write</strong> repository permission, so
        we can&apos;t check which agent keys are already wired. Grant it on
        the App&apos;s{" "}
        <a
          href="https://github.com/settings/installations"
          target="_blank"
          rel="noreferrer"
          className="underline hover:text-amber-100"
        >
          installation page
        </a>{" "}
        (or the org-level equivalent), accept the new permissions on the
        repo, and reload. Meanwhile you can still pick a preset and open
        the seed PR — agent keys can be added later.
      </p>
    );
  }
  if (code === "installation_token_rejected") {
    return (
      <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-snug text-amber-200">
        GitHub rejected our installation token (401). The Ship App may
        have been suspended or reinstalled since the wizard started —
        try reinstalling from step 1 and come back here.
      </p>
    );
  }
  if (code === "actions_disabled") {
    return (
      <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-snug text-amber-200">
        GitHub Actions is disabled on this repo, so there&apos;s no
        secrets store to probe. Enable Actions in repo Settings → Actions
        → General and reload.
      </p>
    );
  }
  return (
    <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-snug text-amber-200">
      Couldn&apos;t probe GitHub Actions secrets on this repo:{" "}
      <code className="font-mono">{message}</code>. Most often this means
      the Ship GitHub App is missing the{" "}
      <strong>Secrets: read &amp; write</strong> repository permission.
      Grant it on the App&apos;s installation page and reload. You can
      still pick a preset and open the seed PR — agent keys can be added
      later.
    </p>
  );
}

function extractProbeCode(message: string): string | null {
  // ``formatProbeError`` shapes the string as ``HTTP <n> — {"code":"...",...}``.
  // Fish the JSON out; be lenient because fetch layers may truncate
  // or stringify the payload slightly differently across runtimes.
  const m = message.match(/\{.*\}/);
  if (!m) return null;
  try {
    const parsed = JSON.parse(m[0]) as { code?: unknown };
    return typeof parsed.code === "string" ? parsed.code : null;
  } catch {
    return null;
  }
}

function missingBlockers(
  preset: PresetId | null,
  missing: ApiAgentSecretStatus[],
) {
  const bits: string[] = [];
  if (!preset) bits.push("pick a preset");
  if (missing.length > 0) {
    const names = missing.map((a) => a.secret_name).filter(Boolean).join(", ");
    bits.push(`push ${names}`);
  }
  if (bits.length === 0) return "Save pending changes.";
  return `${bits.join(" · ")}.`;
}
