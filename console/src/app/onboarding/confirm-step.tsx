/**
 * ConfirmStep — Wave-8c "Confirm bootstrap" wizard step.
 *
 * Replaces the per-repo preset configure step. Shows the operator
 * what the wizard is about to do *before* they hit "Open seed PR":
 *
 *   1. The canonical Plays bundle (sourced live from
 *      ``GET /v1/catalog/default-bundle`` so this UI stays in lockstep
 *      with ``backend.app.services.lane_recipes.DEFAULT_BUNDLE``).
 *   2. How the seed PR lands (infra first, then GitHub Actions opens
 *      a second generated-knowledge PR after merge).
 *   3. The list of activated repos with their tracker / secret status
 *      and the per-repo "Open seed PR" CTA (rendered by
 *      :class:`RepoCard`).
 *
 * Server component — the bundle preview is fetched at render time
 * (no client roundtrip), and the per-repo cards hydrate from
 * ``RepoCardInitial`` rows the page collected up-front in the same
 * way the legacy configure step did. Only the seed CTA itself is
 * client-side; everything else is markup.
 */

import Link from "next/link";

import {
  ApiHttpError,
  ApiUnavailableError,
  getDefaultBundle,
  listIntegrations,
  listWorkspaces,
  type ApiDefaultBundleEntry,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { RepoCard, type RepoCardInitial } from "./repo-card";
import {
  WorkspaceDefaultsPanel,
  type WorkspaceDefaultsPanelInitial,
} from "./workspace-defaults-panel";

export interface ConfirmStepProps {
  workspaceId: string;
  cards: RepoCardInitial[] | null;
  loadError: string | null;
  /** True when at least one ``GitHubInstallation`` for the workspace
   *  is active (the wizard can't reach this step otherwise, but the
   *  panel uses this for the orchestrator-status row). */
  githubAppInstalled: boolean;
}

const CONFIGURE_ERRORS: Record<string, string> = {
  load_failed:
    "Couldn't load your activated repos. Refresh; if it persists, check the backend is reachable.",
};

export async function ConfirmStep({
  workspaceId,
  cards,
  loadError,
  githubAppInstalled,
}: ConfirmStepProps) {
  const message = loadError ? CONFIGURE_ERRORS[loadError] ?? loadError : null;
  const total = cards?.length ?? 0;

  // ── Workspace defaults snapshot ──────────────────────────────
  // Server-rendered so the panel hydrates with truthful state in
  // one round-trip — the operator can see what's missing before
  // they click anything. Failures are non-fatal: a missing list
  // collapses to "no tracker" / "pick one" and the panel still
  // renders. The seed CTAs further down catch the same problem
  // with a 412 from the route, so worst case the operator sees
  // an extra inline error.
  const sessionToken_ws = await getSessionToken();
  let panelInitial: WorkspaceDefaultsPanelInitial = {
    workspaceId,
    defaultAgentProfile: null,
    trackerKinds: [],
    githubAppInstalled,
  };
  try {
    const [workspaces, integrations] = await Promise.all([
      listWorkspaces(sessionToken_ws ?? undefined),
      listIntegrations(workspaceId, sessionToken_ws ?? undefined),
    ]);
    const ws = workspaces.find((w) => w.id === workspaceId);
    // Mirror the backend gate: linear/jira need a token (has_secret);
    // github passes through without one (rides on App install).
    const trackerKinds = integrations
      .filter((i) =>
        ["linear", "github", "jira"].includes(i.kind as string),
      )
      .filter((i) => i.kind === "github" || i.has_secret)
      .map((i) => i.kind as string);
    panelInitial = {
      workspaceId,
      defaultAgentProfile: ws?.default_agent_profile ?? null,
      trackerKinds,
      githubAppInstalled,
    };
  } catch {
    // Keep panelInitial defaults — UI will render "missing" rows
    // and let the operator click through to fix.
  }

  // Fetch the canonical Plays bundle preview server-side. The endpoint
  // is workspace-agnostic but workspace-auth-gated; pass through the
  // session token so SSR doesn't 401. Failures are non-fatal — we
  // render a minimal fallback list so the operator at least knows the
  // wizard will install *something*.
  const sessionToken = await getSessionToken();
  let bundle: ApiDefaultBundleEntry[] = [];
  let bundleError: string | null = null;
  try {
    const resp = await getDefaultBundle(sessionToken ?? undefined);
    bundle = resp.bundle;
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      bundleError = "Backend not reachable — can't preview the install bundle.";
    } else if (err instanceof ApiHttpError) {
      bundleError = `Couldn't load the install bundle (HTTP ${err.status}).`;
    } else {
      bundleError = "Couldn't load the install bundle.";
    }
  }

  return (
    <section data-testid="onboarding-step-confirm">
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Step 4 of 4 &middot; Confirm bootstrap
      </p>
      <h1 className="mt-2 font-display text-4xl font-bold leading-tight">
        One PR per repo. Here&apos;s exactly what lands.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        Ship installs the same canonical bundle of Plays in every new
        repo — no preset menu to second-guess. Review what&apos;s in
        the bundle and how the PR ships, then open it per-repo
        whenever you&apos;re ready.
      </p>

      {message && (
        <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {message}
        </div>
      )}

      {/* ── What gets installed ──────────────────────────────── */}
      <div className="mt-7 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="font-display text-lg font-bold text-white">
            What gets installed in each repo
          </h2>
          <span className="text-[11px] text-white/45">
            {bundle.length > 0
              ? `${bundle.length} Plays`
              : bundleError
                ? "preview unavailable"
                : "loading"}
          </span>
        </div>
        {bundleError && (
          <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-snug text-amber-200">
            {bundleError} The seed PR still installs the same bundle —
            the preview here is best-effort.
          </p>
        )}
        {bundle.length > 0 && (
          <ul className="mt-3 space-y-2">
            {bundle.map((entry) => (
              <li
                key={entry.key}
                data-testid="onboarding-confirm-bundle-item"
                className="rounded-xl border border-white/5 bg-white/[0.02] p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-white">
                    {entry.title}
                  </span>
                  <code className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-white/45">
                    {entry.key}
                  </code>
                </div>
                {entry.reason && (
                  <p className="mt-1 text-[12px] leading-snug text-white/65">
                    {entry.reason}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ── How it lands ─────────────────────────────────────── */}
      <div className="mt-5 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
        <h2 className="font-display text-lg font-bold text-white">
          How it lands
        </h2>
        <ul className="mt-3 space-y-2 text-[12px] leading-relaxed text-white/75">
          <li className="flex gap-2">
            <span className="mt-[2px] grid h-4 w-4 shrink-0 place-items-center rounded-full bg-aqua/20 text-[10px] text-aqua">
              1
            </span>
            <span>
              <strong className="text-white">One pull request per repo</strong>{" "}
              — titled{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                Ship: bootstrap
              </code>
              .
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-[2px] grid h-4 w-4 shrink-0 place-items-center rounded-full bg-aqua/20 text-[10px] text-aqua">
              2
            </span>
            <span>
              <code className="rounded bg-white/5 px-1 text-aqua">
                .ship/config.yml
              </code>{" "}
              +{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                .github/workflows
              </code>{" "}
              files and bootstrap workflow in one commit, ready to merge.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-[2px] grid h-4 w-4 shrink-0 place-items-center rounded-full bg-aqua/20 text-[10px] text-aqua">
              3
            </span>
            <span>
              <strong className="text-white">
                Post-merge bootstrap workflow
              </strong>{" "}
              runs from{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                .github/workflows/ship-bootstrap.yml
              </code>{" "}
              after PR 1 lands, using GitHub Actions as the orchestrator.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-[2px] grid h-4 w-4 shrink-0 place-items-center rounded-full bg-aqua/20 text-[10px] text-aqua">
              4
            </span>
            <span>
              <strong className="text-white">
                A second generated knowledge PR opens
              </strong>{" "}
              with reviewable{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                .ship/knowledge/*.md
              </code>{" "}
              files based on the merged repository.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-[2px] grid h-4 w-4 shrink-0 place-items-center rounded-full bg-aqua/20 text-[10px] text-aqua">
              5
            </span>
            <span>
              <strong className="text-white">GitHub Actions secrets</strong>{" "}
              for Ship — when you click{" "}
              <em>Open seed PR</em>, the backend writes{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                SHIP_RUN_TOKEN
              </code>
              ,{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                SHIP_API_BASE
              </code>
              , and (on first mint){" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                SHIP_API_TOKEN
              </code>{" "}
              so CI can reach your Ship deployment without manual copy-paste.
            </span>
          </li>
        </ul>
      </div>

      {/* ── Workspace defaults gate ───────────────────────────
          Three workspace-level invariants the seed PR depends on.
          Pre-fix the route happily seeded against NULL defaults and
          shipctl runtime then couldn't resolve a coding agent / find
          a tracker — the failure was invisible until cron started
          claiming windows that nothing answered. The panel surfaces
          the missing pieces before the operator clicks Re-seed. */}
      <div className="mt-7">
        <WorkspaceDefaultsPanel initial={panelInitial} />
      </div>

      {/* ── Repos by drift state ───────────────────────────────
          Splitting the list keeps "Repos waiting for bootstrap" honest:
          a never-seeded repo or one stuck on an outdated bundle is the
          actionable case; up-to-date repos go below in a folded section
          so the operator sees green-zone repos at a glance without
          their drift state being mixed with action items.

          ``installed_bundle_version === current_bundle_version``
          counts as up-to-date; ``null`` (never seeded) and any drift
          go in the actionable bucket. The drift case is the
          load-bearing one — pre-fix the wizard rendered every
          activated repo as actionable regardless of bundle, so a
          repo stranded on an outdated workflow file looked
          identical to a fresh activation. */}
      {(() => {
        const actionable = (cards ?? []).filter(
          (c) =>
            c.repo.installed_bundle_version === null ||
            c.repo.installed_bundle_version !== c.repo.current_bundle_version,
        );
        const upToDate = (cards ?? []).filter(
          (c) =>
            c.repo.installed_bundle_version !== null &&
            c.repo.installed_bundle_version === c.repo.current_bundle_version,
        );
        return (
          <>
            <div className="mt-7">
              <h2 className="font-display text-lg font-bold text-white">
                Repos waiting for bootstrap{" "}
                <span className="text-[11px] font-normal text-white/45">
                  ({actionable.length}{" "}
                  {actionable.length === 1 ? "repo" : "repos"})
                </span>
              </h2>

              {actionable.length > 0 && (
                <div className="mt-3 space-y-4">
                  {actionable.map((c) => (
                    <RepoCard
                      key={c.repo.id}
                      workspaceId={workspaceId}
                      initial={c}
                    />
                  ))}
                </div>
              )}

              {actionable.length === 0 && upToDate.length === 0 && !loadError && (
                <div className="mt-3 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-xs text-white/70">
                  No activated repos. Step back to <em>Pick repos</em> and
                  activate at least one before bootstrapping.
                </div>
              )}

              {actionable.length === 0 && upToDate.length > 0 && (
                <div className="mt-3 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-aqua/90">
                  All activated repos are on the current bundle. Nothing to
                  do here.
                </div>
              )}
            </div>

            {upToDate.length > 0 && (
              <details className="mt-6 group/uptodate">
                <summary className="cursor-pointer text-[11px] text-white/45 transition hover:text-white/75 list-none [&::-webkit-details-marker]:hidden">
                  <span className="inline-flex items-center gap-1">
                    <span
                      aria-hidden
                      className="transition-transform group-open/uptodate:rotate-90"
                    >
                      ▸
                    </span>
                    <span>
                      {upToDate.length} repo
                      {upToDate.length === 1 ? "" : "s"} up to date
                    </span>
                  </span>
                </summary>
                <div className="mt-3 space-y-4">
                  {upToDate.map((c) => (
                    <RepoCard
                      key={c.repo.id}
                      workspaceId={workspaceId}
                      initial={c}
                    />
                  ))}
                </div>
              </details>
            )}
          </>
        );
      })()}

      <div className="mt-8 flex items-center justify-between gap-3 border-t border-white/10 pt-5">
        <span className="text-[11px] text-white/45">
          {total > 0
            ? `${total} repo${total === 1 ? "" : "s"} ready. Seed PRs don't auto-merge — you're in control.`
            : "Nothing to bootstrap yet."}
        </span>
        <div className="flex items-center gap-3">
          <Link
            href={`/onboarding?step=repos&ws=${encodeURIComponent(workspaceId)}`}
            className="text-xs text-white/55 hover:text-white"
          >
            &larr; Back to repo picker
          </Link>
        </div>
      </div>
    </section>
  );
}
