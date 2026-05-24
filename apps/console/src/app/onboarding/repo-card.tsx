"use client";

/**
 * RepoCard — per-repo bootstrap card for the Wave-8c "Confirm
 * bootstrap" wizard step.
 *
 * Wave-8c collapses the 14-preset menu: every repo now lands on the
 * canonical Plays bundle (``DEFAULT_BUNDLE``) so this card no longer
 * carries a preset radio. It's purely a status surface plus one CTA:
 *
 *   - Tracker binding (re-uses the pre-Wave-8c chip + form).
 *   - Agent secrets (re-uses the pre-Wave-8c catalog + paste form).
 *   - "Open seed PR" CTA — disabled until tracker / required secrets
 *     are ready, primary once the gate clears. POSTs to
 *     ``/api/onboard/wizard-seed`` with just ``{workspace_id, repo_id}``;
 *     the backend uses ``DEFAULT_BUNDLE`` regardless.
 *
 * On a successful seed we stash the full :type:`ApiWizardSeedResult`
 * in ``sessionStorage`` under
 * ``ship.wizard_seed_result.<repo_id>`` and navigate to
 * ``/onboarding?step=done&ws=<wsId>&repo_id=<id>``. The done step
 * (P5-09) reads from sessionStorage and falls back to the latest
 * wizard_seed audit row if missing (e.g. user reloaded the tab).
 *
 * State model
 * -----------
 *
 * Backend is the source of truth. On mount the card renders the
 * server-provided snapshot; tracker/secret mutations refresh local
 * state from the API response. The seed CTA is one-shot — once the
 * PR is open we navigate away rather than rendering a "seeded" row,
 * because the next step (``done``) owns that summary.
 */

import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";

import type {
  ApiAgentSecretStatus,
  ApiTrackerBinding,
  ApiWizardSeedResult,
  TrackerKind,
} from "@/lib/api/client";
import { Badge, type BadgeTone } from "@/components/ui";
import { SdlcReadinessCard } from "@/components/sdlc-readiness-card";

export interface RepoCardInitial {
  repo: {
    id: string;
    full_name: string;
    default_branch: string;
    /**
     * Bundle version stamped on the repo at the last successful
     * ``wizard_seed`` run. ``null`` means the repo was activated but
     * never seeded (greenfield) OR was seeded before the column
     * existed (legacy). The card uses this against
     * ``current_bundle_version`` to decide ``not_seeded`` /
     * ``up_to_date`` / ``update_available`` styling. Without it
     * every repo looks "ready" even when it's running an outdated
     * starter workflow against a server that's moved on — exactly
     * the dogfood failure on 2026-05-02 (ship-canary stuck on
     * ``v0.5`` with the new shipctl emitting routine vocabulary
     * the legacy workflow file couldn't read).
     */
    installed_bundle_version: string | null;
    /**
     * Server's current canonical bundle version. Mirrored from
     * :data:`backend.app.services.seed_bundle.BUNDLE_VERSION` on
     * every ``ActivatedRepoOut`` so the FE doesn't need a separate
     * meta endpoint.
     */
    current_bundle_version: string;
  };
  tracker: ApiTrackerBinding;
  agents: ApiAgentSecretStatus[];
  /**
   * Non-fatal per-probe failures from the server render. Each entry is a
   * short human-readable message. The card stays interactive — missing
   * tracker data is treated as "none", missing secrets as "unknown" —
   * and surfaces these hints so the operator can reason about what's
   * actually wired vs. probed.
   *
   * Why the fields aren't just absent: users reported the whole step
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

/** sessionStorage key used to hand the seed result over to the done step. */
export function wizardSeedResultStorageKey(repoId: string): string {
  return `ship.wizard_seed_result.${repoId}`;
}

export function RepoCard({
  workspaceId,
  initial,
}: {
  workspaceId: string;
  initial: RepoCardInitial;
}) {
  const router = useRouter();

  // ── Local state ───────────────────────────────────────────────
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

  const [seedSaving, setSeedSaving] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);
  const seedButtonRef = useRef<HTMLButtonElement | null>(null);
  // Post-seed modal state. Pre-fix the seed POST navigated straight
  // to ``/done`` and silently dropped the operator on a screen that
  // told them "open the PR on GitHub and merge it" — ~30% of pilot
  // operators never came back. The modal asks "want me to turn it on
  // for you?" while the PR URL is still in their head; accepting it
  // calls ``/wizard-seed/activate`` which merges via the App token.
  const [pendingSeed, setPendingSeed] = useState<ApiWizardSeedResult | null>(
    null,
  );
  const [activating, setActivating] = useState(false);
  const [activateError, setActivateError] = useState<string | null>(null);
  const [activateBlocked, setActivateBlocked] = useState<string | null>(null);

  // ── Derived ───────────────────────────────────────────────────
  const missingRequiredSecrets = useMemo(
    () => agents.filter((a) => a.required && !a.present),
    [agents],
  );

  // Tracker is considered "ready" if any kind is wired (per-repo or
  // workspace-default fallback). The wizard never blocks on tracker
  // because the backend tolerates ``tracker_kind=null`` — the FSM
  // doc just renders a "not connected yet" header. We surface it as
  // a hint, not a hard gate.
  const trackerReady = Boolean(tracker.kind || tracker.workspace_default_kind);

  const readyToSeed = Boolean(
    missingRequiredSecrets.length === 0 &&
      !seedSaving &&
      !secretsSaving &&
      !trackerSaving,
  );

  // ── Bundle drift state ───────────────────────────────────────
  // ``installed_bundle_version`` lives on every ``ActivatedRepoOut``
  // (stamped by ``wizard_seed`` post-composer) alongside the server's
  // canonical ``current_bundle_version``. The card derives one of
  // four states from the pair:
  //
  //   - ``not_seeded``     — installed === null
  //   - ``up_to_date``     — installed === current
  //   - ``update_available`` — installed !== current
  //   - ``unknown``        — fallback (shouldn't happen but kept so a
  //                          missing field never crashes the render)
  //
  // ``update_available`` is the load-bearing one — the dogfood failure
  // on 2026-05-02 (ship-canary stuck on v0.5 with the new shipctl
  // emitting ``routine`` vocabulary the legacy workflow file
  // couldn't read) was invisible because the card just rendered
  // "READY". The amber banner + Re-seed CTA make the drift
  // discoverable without an audit-log spelunk.
  const installed = initial.repo.installed_bundle_version;
  const current = initial.repo.current_bundle_version;
  const seedState: "not_seeded" | "up_to_date" | "update_available" | "unknown" =
    installed === null
      ? "not_seeded"
      : installed === current
        ? "up_to_date"
        : "update_available";

  return (
    <section
      className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5 shadow-card"
      data-testid="onboarding-confirm-repo-card"
      data-repo-id={initial.repo.id}
      data-repo-full-name={initial.repo.full_name}
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-white">
              {initial.repo.full_name}
            </h3>
            <span className="font-mono text-[10px] uppercase tracking-widest text-white/35">
              {initial.repo.default_branch}
            </span>
            {tracker.kind && tracker.source === "workspace" && (
              <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-white/45">
                inherits {tracker.kind}
              </span>
            )}
            {tracker.kind && tracker.source === "repo" && (
              <span className="rounded bg-aqua/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-aqua">
                {tracker.kind}
              </span>
            )}
            {seedState === "not_seeded" && (
              <span className="rounded bg-coral/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-coral">
                not seeded
              </span>
            )}
            {seedState === "up_to_date" && (
              <span className="rounded bg-aqua/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-aqua">
                v{current}
              </span>
            )}
            {seedState === "update_available" && (
              <span className="rounded bg-amber-500/20 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-amber-200">
                v{installed} → v{current}
              </span>
            )}
          </div>
        </div>
        {(() => {
          const ready = readyToSeed && trackerReady;
          const updateAvailable = seedState === "update_available";
          const missingSecrets = missingRequiredSecrets.length;
          let tone: BadgeTone = "neutral";
          let label = "pending";
          if (updateAvailable) {
            tone = "warn";
            label = "update available";
          } else if (ready) {
            tone = "ok";
            label = "ready";
          } else if (missingSecrets > 0) {
            tone = "err";
            label = `${missingSecrets} secret${missingSecrets === 1 ? "" : "s"} missing`;
          } else if (!trackerReady) {
            label = "no tracker";
          }
          return <Badge tone={tone}>{label}</Badge>;
        })()}
      </header>

      {seedState === "update_available" && (
        // Drift banner. Re-seed is just the existing /wizard_seed
        // POST — the same CTA at the bottom — but framed up-front
        // as "your repo is behind the canonical bundle" rather
        // than "let's bootstrap". The seed PR is idempotent: it
        // overwrites the workflow file, the .ship/ config, the FSM
        // doc, and stamps the new ``installed_bundle_version`` on
        // commit. The customer just needs to merge it.
        <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-[11px] leading-snug text-amber-200">
          <strong className="text-amber-100">Bundle update available.</strong>{" "}
          This repo is on{" "}
          <code className="rounded bg-white/5 px-1 text-amber-100">
            v{installed}
          </code>{" "}
          while the canonical bundle is now{" "}
          <code className="rounded bg-white/5 px-1 text-amber-100">
            v{current}
          </code>
          . Open a re-seed PR below to refresh{" "}
          <code className="rounded bg-white/5 px-1">.ship/config.yml</code>,
          the schedule trigger workflow, and the tracker FSM. The PR
          is idempotent — review the diff before you merge.
        </div>
      )}

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
            // OAuth-only providers (Linear today; Notion when it lands
            // on this surface) need a workspace-level OAuth row first.
            // The pill is disabled with a tooltip + deeplink so the
            // operator doesn't fight the backend 412 on save.
            const oauthGated = k === "linear";
            const oauthMissing =
              oauthGated && tracker.workspace_oauth_connected === false;
            const disabled = oauthMissing;
            const baseClass =
              "rounded-full border px-3 py-1 text-xs font-medium transition";
            const stateClass = selected
              ? "border-aqua/60 bg-aqua/[0.1] text-aqua"
              : disabled
                ? "border-white/[0.08] bg-white/[0.02] text-white/30 cursor-not-allowed"
                : "border-white/[0.08] bg-white/[0.04] text-white/70 hover:border-aqua/30";
            return (
              <button
                key={k}
                type="button"
                disabled={disabled}
                title={
                  oauthMissing
                    ? "Connect Linear OAuth at the workspace level first"
                    : undefined
                }
                onClick={() =>
                  setTrackerDraft((d) => ({
                    ...d,
                    kind: selected ? "" : k,
                  }))
                }
                className={`${baseClass} ${stateClass}`}
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
          (() => {
            // Linear team picker. The legacy "type a team key" text
            // input was misleading (sounded like an API key) and bus-
            // factored — operators couldn't tell which team slugs
            // were valid. The OAuth callback persists the discovered
            // teams as ``workspace_default_config.team_options``; we
            // surface them as a dropdown here.
            const opts = tracker.workspace_default_config?.team_options ?? [];
            if (opts.length === 0) {
              return (
                <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-snug text-amber-200">
                  No Linear teams discovered yet. Re-run the workspace
                  Linear OAuth flow so we can fetch the team list.
                </p>
              );
            }
            return (
              <select
                value={trackerDraft.team}
                onChange={(e) =>
                  setTrackerDraft((d) => ({ ...d, team: e.target.value }))
                }
                className="mt-2 w-full rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-xs text-white"
              >
                <option value="">— Pick a Linear team —</option>
                {opts.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.name} ({opt.key})
                  </option>
                ))}
              </select>
            );
          })()
        )}
        {trackerDraft.kind === "jira" && (
          <input
            type="text"
            placeholder="Jira project key (e.g. WIDG)"
            value={trackerDraft.project}
            onChange={(e) =>
              setTrackerDraft((d) => ({ ...d, project: e.target.value }))
            }
            className="mt-2 w-full rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-xs text-white placeholder-white/35"
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

      {/* ── Agent secrets ────────────────────────────────────────
          Collapsed by default — most operators don't touch agent keys
          on the per-repo card (workspace defaults cover Claude Code +
          Cursor) and the long catalog crowded the seed CTA. Open the
          panel when an agent surfaces a "missing" pill in the
          summary. */}
      <details
        className="mt-5 group/agents"
        open={missingRequiredSecrets.length > 0 || secretsFailed.length > 0}
      >
        <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
          <span className="inline-flex items-center gap-2">
            <span
              aria-hidden
              className="text-white/40 transition-transform group-open/agents:rotate-90"
            >
              ▸
            </span>
            <span className="text-[11px] font-bold uppercase tracking-widest text-white/55">
              Agents
            </span>
            <span className="text-[11px] text-white/45">
              {(() => {
                const required = agents.filter((a) => a.required);
                const present = required.filter((a) => a.present).length;
                const missing = required.length - present;
                if (missing > 0)
                  return `${missing} required key${missing === 1 ? "" : "s"} missing`;
                if (required.length === 0) return "no required keys";
                return `${present}/${required.length} required keys present`;
              })()}
            </span>
          </span>
        </summary>
        <p className="mt-2 text-[11px] text-white/55">
          Pick <strong className="text-white/80">one</strong> coding-agent key
          — Claude Code (Anthropic), Cursor Cloud, OpenAI Codex,{" "}
          <em>or</em> use GitHub Copilot (no extra secret; it uses the
          runner&apos;s <code className="text-white/60">GITHUB_TOKEN</code>
          ). When you open the seed PR, Ship also writes{" "}
          <code className="text-white/60">SHIP_API_BASE</code> and{" "}
          <code className="text-white/60">SHIP_API_TOKEN</code> to GitHub
          Actions for <code className="text-white/60">shipctl run</code>.
          Anything you paste below goes straight to GitHub and is never stored
          on Ship.
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
                className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3"
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
                      ) : a.secret_name ? (
                        a.present ? (
                          <span className="rounded bg-aqua/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-aqua">
                            present
                          </span>
                        ) : (
                          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-amber-200">
                            optional
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
                {a.secret_name && !a.present && (
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
                      className="min-w-[240px] flex-1 rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 font-mono text-xs text-white placeholder-white/35"
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
      </details>

      {/* ── Seed button ────────────────────────────────────────── */}
      <footer className="mt-6 border-t border-white/[0.08] pt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0 text-[11px] leading-snug text-white/55">
            {!readyToSeed ? (
              <>
                <strong className="text-white/80">Not ready.</strong>{" "}
                {missingBlockers(missingRequiredSecrets)}
              </>
            ) : seedState === "update_available" ? (
              <>
                Re-seed updates Ship&apos;s workflow and config files to{" "}
                <code className="text-white/60">v{current}</code>. Review
                the PR diff like any other change — it&apos;s idempotent.
              </>
            ) : (
              <>
                Ready to turn Ship on. We&apos;ll open one pull request that
                wires Ship to this repo — review and merge like any other PR.
              </>
            )}
          </div>
          <button
            ref={seedButtonRef}
            type="button"
            data-testid="onboarding-confirm-open-seed-pr"
            data-repo-id={initial.repo.id}
            data-repo-full-name={initial.repo.full_name}
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
                }),
              });
              if (!resp.ok) {
                setSeedSaving(false);
                const p = await resp.json().catch(() => ({}));
                setSeedError(p?.error ?? "unknown");
                return;
              }
              const body = (await resp.json()) as {
                result: ApiWizardSeedResult;
              };
              // Hand the result to the done step (P5-09) via
              // sessionStorage. Key is per-repo so multiple seeds in
              // one wizard session don't clobber each other; the
              // done step reads by ``?repo_id=...``.
              try {
                const key = wizardSeedResultStorageKey(initial.repo.id);
                sessionStorage.setItem(key, JSON.stringify(body.result));
              } catch {
                // sessionStorage can throw in private mode / when
                // quota is exhausted. Non-fatal — the done step has
                // an audit-log fallback.
              }
              setSeedSaving(false);
              // Pop the activation modal instead of jumping straight
              // to /done. The two CTAs there decide whether we merge
              // for the operator (calls /wizard-seed/activate) or let
              // them merge it themselves on github.com.
              setPendingSeed(body.result);
            }}
            className="rounded-md bg-aqua/15 px-3 py-1.5 text-sm font-semibold text-aqua transition hover:bg-aqua/25 disabled:cursor-default disabled:opacity-40"
          >
            {seedSaving
              ? "Opening PR..."
              : seedState === "update_available"
                ? "Open re-seed PR"
                : "Open seed PR"}
          </button>
        </div>
        {seedError && (
          <div className="mt-2 space-y-1.5 rounded-md border border-coral/30 bg-coral/[0.08] p-2.5 text-[11px] text-coral">
            <p>
              Seed PR didn&apos;t open ({seedError}). Try again once the
              blocker clears.
            </p>
            <div className="flex flex-wrap items-center gap-3 pt-1">
              <button
                type="button"
                onClick={() => {
                  setSeedError(null);
                  seedButtonRef.current?.click();
                }}
                className="rounded-md bg-aqua/15 px-2.5 py-1 text-xs font-semibold text-aqua hover:bg-aqua/25"
              >
                Retry seed PR
              </button>
              <a
                href={`mailto:support@elmundi.com?subject=${encodeURIComponent(
                  `[Ship] wizard seed failed — ${initial.repo.full_name}`,
                )}&body=${encodeURIComponent(
                  [
                    "Ship's wizard seed step failed and I can't unblock it.",
                    "",
                    `Workspace: ${workspaceId}`,
                    `Repo: ${initial.repo.full_name}`,
                    `Error: ${seedError}`,
                  ].join("\n"),
                )}`}
                className="text-[11px] font-semibold underline-offset-2 hover:underline"
              >
                Tell us what happened →
              </a>
            </div>
          </div>
        )}
      </footer>

      {/* BS5 — SDLC readiness + bootstrap CTA right in the wizard, so a
          freshly-connected repo can be brought up to its project-type
          standard without leaving onboarding. Lazy: nothing fetches
          until the operator clicks "Check readiness". */}
      <div className="mt-5 border-t border-white/[0.06] pt-4">
        <p className="mb-2 text-[11px] font-bold uppercase tracking-widest text-white/55">
          SDLC readiness
        </p>
        <SdlcReadinessCard
          workspaceId={workspaceId}
          repoId={initial.repo.id}
          repoFullName={initial.repo.full_name}
        />
      </div>

      {pendingSeed && (
        <ActivationModal
          repoFullName={initial.repo.full_name}
          seed={pendingSeed}
          activating={activating}
          error={activateError}
          blockedMessage={activateBlocked}
          onActivate={async () => {
            setActivating(true);
            setActivateError(null);
            setActivateBlocked(null);
            const resp = await fetch("/api/onboard/wizard-seed-activate", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                workspace_id: workspaceId,
                repo_id: initial.repo.id,
                pr_number: pendingSeed.pr_number,
              }),
            });
            setActivating(false);
            if (!resp.ok) {
              const p = await resp.json().catch(() => ({}));
              if (resp.status === 409) {
                // Branch protection / required checks — surface the
                // friendly fallback instead of a red error toast.
                setActivateBlocked(
                  typeof p?.github_message === "string"
                    ? p.github_message
                    : "Branch protection or required checks are blocking the merge.",
                );
                return;
              }
              setActivateError(
                typeof p?.error === "string" ? p.error : "unknown",
              );
              return;
            }
            // Done-page badge needs to know the merge actually
            // landed (not just that the user *intended* to merge).
            // Stash the activation result under its own
            // sessionStorage key — separate from
            // ``ship.wizard_seed_result.{id}`` whose invariant is
            // "configure step writes, done page reads, nobody
            // mutates after". Best-effort: a swallowed write means
            // the badge falls back to OPENED, which is honest.
            try {
              const body = (await resp.json().catch(() => null)) as
                | { merged?: unknown; sha?: unknown }
                | null;
              if (body && body.merged === true) {
                window.sessionStorage.setItem(
                  `ship.wizard_seed_activated.${initial.repo.id}`,
                  JSON.stringify({
                    merged: true,
                    sha: typeof body.sha === "string" ? body.sha : null,
                  }),
                );
              }
            } catch {
              // sessionStorage disabled / private browsing —
              // navigate anyway; the badge degrades to OPENED.
            }
            navigateToDone();
          }}
          onSkip={() => {
            navigateToDone();
          }}
          onDismiss={() => {
            // Backdrop click / X — same as Skip; the seed PR is
            // already open, the operator just doesn't want Ship to
            // merge for them right now.
            navigateToDone();
          }}
        />
      )}
    </section>
  );

  function navigateToDone() {
    const url = new URL("/onboarding", window.location.origin);
    url.searchParams.set("step", "done");
    url.searchParams.set("ws", workspaceId);
    url.searchParams.set("repo_id", initial.repo.id);
    router.push(url.pathname + url.search);
  }
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
        repo, and reload. Meanwhile you can still open the seed PR —
        agent keys can be added later.
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
      still open the seed PR — agent keys can be added later.
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

function missingBlockers(missing: ApiAgentSecretStatus[]) {
  if (missing.length === 0) return "Save pending changes.";
  const names = missing.map((a) => a.secret_name).filter(Boolean).join(", ");
  return `push ${names}.`;
}

/**
 * ActivationModal — pops after a successful seed PR with two CTAs.
 *
 * Copy is deliberately PO-flavoured: the user who just clicked
 * "Set up Ship" doesn't think of the next step as "merge a PR",
 * they think of it as "turn the thing on". So the primary CTA is
 * "Activate Ship now" (calls the merge endpoint via the App token);
 * the secondary is "I'll review the PR myself" (navigates to /done
 * which shows the PR link + status). Either path lands the operator
 * on the same /done summary so the post-onboarding flow stays
 * consistent regardless of choice.
 *
 * Rendered as a fixed-position overlay rather than a portal because
 * the wizard is single-page; we don't have to worry about parent
 * stacking contexts elsewhere on the route.
 */
function ActivationModal({
  repoFullName,
  seed,
  activating,
  error,
  blockedMessage,
  onActivate,
  onSkip,
  onDismiss,
}: {
  repoFullName: string;
  seed: ApiWizardSeedResult;
  activating: boolean;
  error: string | null;
  blockedMessage: string | null;
  onActivate: () => void;
  onSkip: () => void;
  onDismiss: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      data-testid="onboarding-activate-modal"
    >
      <div
        aria-hidden
        className="absolute inset-0 bg-ink/80 backdrop-blur-sm"
        onClick={onDismiss}
      />
      <div className="relative w-full max-w-md rounded-2xl border border-white/[0.08] bg-ink p-6 shadow-card">
        <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-aqua/85">
          PR opened &middot; #{seed.pr_number}
        </p>
        <h2 className="mt-2 font-display text-2xl font-bold leading-tight text-white">
          Merge the PR now?
        </h2>
        <p className="mt-3 text-[13px] leading-relaxed text-white/75">
          The pull request is open in{" "}
          <code className="rounded bg-white/5 px-1 text-aqua">{repoFullName}</code>
          . If you&apos;d rather skip the GitHub roundtrip, I can merge
          it for you right now &mdash; same outcome, one fewer click.
        </p>
        <p className="mt-2 text-[11px] leading-snug text-white/45">
          You can always review it on GitHub first. The PR is just a
          small config folder plus the workflow file Ship needs.
        </p>

        {blockedMessage && (
          <p
            data-testid="onboarding-activate-blocked"
            className="mt-4 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] leading-snug text-amber-200"
          >
            <strong className="text-amber-100">Branch protection blocked the merge.</strong>{" "}
            {blockedMessage} Open the PR on GitHub to finish merging
            once the required checks pass.
          </p>
        )}

        {error && (
          <p className="mt-4 rounded border border-coral/30 bg-coral/10 px-3 py-2 text-[11px] text-coral">
            Couldn&apos;t merge ({error}). Open the PR on GitHub to
            review and merge it manually.
          </p>
        )}

        <div className="mt-6 flex flex-wrap items-center justify-end gap-3">
          <a
            href={seed.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] text-white/55 hover:text-white"
          >
            Open PR on GitHub ↗
          </a>
          <button
            type="button"
            onClick={onSkip}
            data-testid="onboarding-activate-skip"
            className="rounded-full border border-white/15 bg-white/[0.04] px-4 py-2 text-xs font-semibold text-white/75 transition hover:bg-white/[0.08]"
          >
            I&apos;ll review it myself
          </button>
          <button
            type="button"
            onClick={onActivate}
            disabled={activating || blockedMessage !== null}
            data-testid="onboarding-activate-confirm"
            className="rounded-md bg-aqua/15 px-3 py-1.5 text-sm font-semibold text-aqua transition hover:bg-aqua/25 disabled:cursor-default disabled:opacity-40"
          >
            {activating ? "Activating…" : "Activate Ship now →"}
          </button>
        </div>
      </div>
    </div>
  );
}
