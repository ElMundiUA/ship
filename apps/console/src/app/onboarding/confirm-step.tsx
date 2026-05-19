/**
 * ConfirmStep — final wizard step "Activate Ship".
 *
 * PO-friendly rewrite (May 2026): the previous version listed every
 * Play in the bundle and a 4-step "how it lands" walkthrough with
 * file paths, secret names, and bundle versions. Operators on the
 * non-eng side bounced off it. The new layout leads with the action
 * (seed-PR CTAs per repo) and tucks the technical detail behind a
 * collapsible "What's actually in the PR?" disclosure for those who
 * want it.
 *
 * Workspace-defaults panel moved to the Roles step (step 4) so the
 * "what plays which role" decision is consolidated there.
 *
 * Server component — the bundle preview is fetched at render time
 * (no client roundtrip), and the per-repo cards hydrate from
 * ``RepoCardInitial`` rows the page collected up-front. Only the
 * seed CTA itself is client-side; everything else is markup.
 */

import Link from "next/link";

import {
  ApiHttpError,
  ApiUnavailableError,
  getDefaultProcess,
  type ApiDefaultProcessEntry,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { RepoCard, type RepoCardInitial } from "./repo-card";

export interface ConfirmStepProps {
  workspaceId: string;
  cards: RepoCardInitial[] | null;
  loadError: string | null;
  /** True when at least one ``GitHubInstallation`` for the workspace
   *  is active. Currently unused on this step (Roles step owns the
   *  workspace-defaults panel now) but kept on the props so the
   *  caller in ``page.tsx`` doesn't have to change shape. */
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
  githubAppInstalled: _githubAppInstalled,
}: ConfirmStepProps) {
  const message = loadError ? CONFIGURE_ERRORS[loadError] ?? loadError : null;
  const total = cards?.length ?? 0;

  // Fetch the canonical Plays bundle preview server-side. The endpoint
  // is workspace-agnostic but workspace-auth-gated; pass through the
  // session token so SSR doesn't 401. Failures are non-fatal — we
  // render a minimal fallback list so the operator at least knows the
  // wizard will install *something*.
  const sessionToken = await getSessionToken();
  let bundle: ApiDefaultProcessEntry[] = [];
  let bundleError: string | null = null;
  try {
    const resp = await getDefaultProcess(sessionToken ?? undefined);
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
        Step 5 of 5 &middot; Activate Ship
      </p>
      <h1 className="mt-2 font-display text-2xl font-bold leading-tight">
        Turn Ship on in your repos.
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        For each repo Ship opens a single pull request. It drops in a
        small config folder and the workflow file Ship needs, and
        pushes credentials so GitHub Actions can talk to Ship. Review
        it like any other PR &mdash; merge when you&apos;re happy.
      </p>

      {message && (
        <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {message}
        </div>
      )}

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
                Repos to activate{" "}
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
                <div className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.02] px-4 py-3 text-xs text-white/70">
                  No repos picked yet. Go back to <em>Repos</em> and pick
                  the ones you want Ship to work with first.
                </div>
              )}

              {actionable.length === 0 && upToDate.length > 0 && (
                <div className="mt-3 rounded-xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-xs text-aqua/90">
                  Everything&apos;s already on the latest version. Nothing to do here.
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

      {/* ── Bundle peek ───────────────────────────────────────
          Folded by default — the average operator doesn't need to
          read the Plays catalogue before clicking "Activate". The
          ``<details>`` is here so power users can sanity-check what
          actually lands in the PR without leaving the wizard. */}
      <details className="mt-8 group/bundle rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
        <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
          <span className="inline-flex items-center gap-2 text-[12px] text-white/65 hover:text-white">
            <span
              aria-hidden
              className="transition-transform group-open/bundle:rotate-90"
            >
              ▸
            </span>
            <span>What&apos;s actually in the PR?</span>
            {bundle.length > 0 && (
              <span className="text-[11px] text-white/40">
                {bundle.length} Plays
              </span>
            )}
          </span>
        </summary>
        <p className="mt-3 text-[12px] leading-relaxed text-white/65">
          Ship installs the same standard set of automations in every
          repo &mdash; they&apos;re what turn on inbox triage,
          scheduled checks, and the agent that picks up tickets. Plus
          one tiny workflow file under{" "}
          <code className="rounded bg-white/5 px-1 text-aqua">
            .github/workflows/
          </code>{" "}
          and a <code className="rounded bg-white/5 px-1 text-aqua">.ship/</code>{" "}
          config folder. Nothing outside those two paths is touched.
        </p>
        {bundleError && (
          <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-snug text-amber-200">
            {bundleError} The PR still installs the same set &mdash; the
            preview here is best-effort.
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
      </details>

      {/* Footer cross-links. The stepper handles ordered navigation
          between adjacent setup steps, so this row carries the two
          escape valves that aren't on the stepper:

          - ``← Hub`` returns to the post-setup landing page
            (only meaningful for already-seeded workspaces, but
            keeping it always-visible avoids state-checking just
            to hide a 6-character link).
          - ``← Dashboard`` exits the wizard entirely. Mirrors
            "Skip to dashboard" in the page header without making
            the operator scroll back up.

          The pre-fix "← Back to repo picker" link is gone because
          the clickable stepper above offers the same hop in the
          same gesture, only consistent with every other cross-step
          jump. */}
      <div className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.08] pt-5">
        <span className="text-[11px] text-white/45">
          {total > 0
            ? `${total} repo${total === 1 ? "" : "s"} ready. You stay in control — every change goes through a PR you can review.`
            : "Nothing to activate yet."}
        </span>
        <div className="flex flex-wrap items-center gap-4 text-xs">
          <Link
            href={`/onboarding?step=hub&ws=${encodeURIComponent(workspaceId)}`}
            className="text-white/55 hover:text-white"
          >
            &larr; Hub
          </Link>
          <Link
            href={`/?ws=${encodeURIComponent(workspaceId)}`}
            className="text-white/55 hover:text-white"
          >
            &larr; Dashboard
          </Link>
        </div>
      </div>
    </section>
  );
}
