/**
 * ConfirmStep — Wave-8c "Confirm bootstrap" wizard step.
 *
 * Replaces the per-repo preset configure step. Shows the operator
 * what the wizard is about to do *before* they hit "Open seed PR":
 *
 *   1. The canonical Plays bundle (sourced live from
 *      ``GET /v1/catalog/default-bundle`` so this UI stays in lockstep
 *      with ``backend.app.services.lane_recipes.DEFAULT_BUNDLE``).
 *   2. How the bootstrap PR lands (one PR per repo, files included,
 *      Inbox routing rules pre-seeded from CODEOWNERS, repo-intel
 *      harvest dispatched).
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
  type ApiDefaultBundleEntry,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { RepoCard, type RepoCardInitial } from "./repo-card";

export interface ConfirmStepProps {
  workspaceId: string;
  cards: RepoCardInitial[] | null;
  loadError: string | null;
}

const CONFIGURE_ERRORS: Record<string, string> = {
  load_failed:
    "Couldn't load your activated repos. Refresh; if it persists, check the backend is reachable.",
};

export async function ConfirmStep({
  workspaceId,
  cards,
  loadError,
}: ConfirmStepProps) {
  const message = loadError ? CONFIGURE_ERRORS[loadError] ?? loadError : null;
  const total = cards?.length ?? 0;

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
    <section>
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
              +{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                .ship/knowledge
              </code>{" "}
              files in one commit, ready to merge.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-[2px] grid h-4 w-4 shrink-0 place-items-center rounded-full bg-aqua/20 text-[10px] text-aqua">
              3
            </span>
            <span>
              <strong className="text-white">
                Inbox routing rules pre-seeded
              </strong>{" "}
              from your{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                CODEOWNERS
              </code>{" "}
              file — every owner becomes a routing target so issues land
              with the right team on day one.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-[2px] grid h-4 w-4 shrink-0 place-items-center rounded-full bg-aqua/20 text-[10px] text-aqua">
              4
            </span>
            <span>
              <strong className="text-white">
                A repository intel harvest runs once
              </strong>{" "}
              and populates{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                .ship/knowledge/repo-intel.md
              </code>{" "}
              so day-zero agent runs already know your repo.
            </span>
          </li>
        </ul>
      </div>

      {/* ── Repos waiting for bootstrap ──────────────────────── */}
      <div className="mt-7">
        <h2 className="font-display text-lg font-bold text-white">
          Repos waiting for bootstrap{" "}
          <span className="text-[11px] font-normal text-white/45">
            ({total} {total === 1 ? "repo" : "repos"})
          </span>
        </h2>

        {cards && cards.length > 0 && (
          <div className="mt-3 space-y-4">
            {cards.map((c) => (
              <RepoCard key={c.repo.id} workspaceId={workspaceId} initial={c} />
            ))}
          </div>
        )}

        {cards && cards.length === 0 && !loadError && (
          <div className="mt-3 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-xs text-white/70">
            No activated repos. Step back to <em>Pick repos</em> and activate
            at least one before bootstrapping.
          </div>
        )}
      </div>

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
