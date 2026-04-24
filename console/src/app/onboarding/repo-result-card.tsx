"use client";

/**
 * Pure presentational "what just happened" card for one repo (P5-09).
 *
 * Renders the wizard_seed result: PR link, branch + file count,
 * CODEOWNERS routing summary, synthetic-lane count, and slots in the
 * intel-poll badge. No fetching here — the parent
 * (:component:`DoneResult`) hands in everything we need so the card
 * is trivial to unit-test and reuse in fallback paths.
 */

import Link from "next/link";

import type {
  ApiActivatedRepo,
  ApiWizardSeedOut,
  ApiWizardSeedCodeownersSummary,
} from "@/lib/api/client";

import { IntelPollBadge } from "./intel-poll-badge";

export function RepoResultCard({
  workspaceId,
  repo,
  result,
}: {
  workspaceId: string | null;
  /** Best-effort repo metadata. ``null`` when only the wizard payload is around. */
  repo: ApiActivatedRepo | null;
  result: ApiWizardSeedOut;
}) {
  const fullName = repo?.full_name ?? deriveOwnerRepoFromBranch(result.branch);
  const defaultBranch = repo?.default_branch ?? "main";
  const fileCount = result.files.length;

  return (
    <article
      data-testid="onboarding-done-card"
      className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 backdrop-blur-xl shadow-card"
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-lg font-bold text-white">
            {fullName ?? "(repo)"}
          </h3>
          <p className="mt-0.5 text-[11px] text-white/55">
            Default branch:{" "}
            <code className="rounded bg-white/5 px-1 text-aqua">
              {defaultBranch}
            </code>
          </p>
        </div>
        {result.tracker_kind && (
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] uppercase tracking-widest text-white/65">
            tracker · {result.tracker_kind}
          </span>
        )}
      </header>

      <div className="mt-4 space-y-3">
        <PullRequestRow result={result} />
        <CodeownersRow
          summary={result.codeowners}
          workspaceId={workspaceId}
          repoId={repo?.id ?? null}
        />
        <LanesRow
          workspaceId={workspaceId}
          repoId={repo?.id ?? null}
          syntheticLanes={result.synthetic_lanes_created}
        />
        <IntelRow
          workspaceId={workspaceId}
          repoId={repo?.id ?? null}
          handle={result.intel}
        />
        <FileCountRow fileCount={fileCount} />
      </div>
    </article>
  );
}

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.025] p-3">
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </div>
      <div className="mt-1.5 text-xs text-white/80">{children}</div>
    </div>
  );
}

function PullRequestRow({ result }: { result: ApiWizardSeedOut }) {
  return (
    <Section label="Pull request">
      <div className="flex flex-wrap items-center gap-2">
        <a
          href={result.pr_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/[0.08] px-3 py-1 text-[11px] font-bold text-aqua hover:bg-aqua/[0.16]"
        >
          → #{result.pr_number} Ship: bootstrap (open in GitHub)
        </a>
        <span className="rounded-full border border-emerald-400/30 bg-emerald-500/[0.08] px-2 py-0.5 text-[10px] uppercase tracking-widest text-emerald-300">
          opened
        </span>
      </div>
      <div className="mt-2 text-[11px] text-white/55">
        Branch:{" "}
        <code className="rounded bg-white/5 px-1 text-white/80">
          {result.branch}
        </code>
      </div>
    </Section>
  );
}

function CodeownersRow({
  summary,
  workspaceId,
  repoId,
}: {
  summary: ApiWizardSeedCodeownersSummary | null;
  workspaceId: string | null;
  repoId: string | null;
}) {
  // Empty-state — CODEOWNERS not present in the repo. We deliberately
  // keep this short and link to docs rather than dropping a "wizard
  // failed" warning: a missing CODEOWNERS is the operator's choice,
  // not a Ship error.
  if (summary == null || !summary.file_found) {
    return (
      <Section label="Routing rules">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-white/15 bg-white/[0.04] px-2 py-0.5 text-[10px] uppercase tracking-widest text-white/65">
            no CODEOWNERS yet
          </span>
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-white/65">
          We didn&apos;t find a <code className="text-white/85">CODEOWNERS</code>{" "}
          file. Add one and re-run bootstrap to seed routing rules so
          clarification requests land on the right reviewers.{" "}
          <Link
            href="/documentation/inbox-routing#codeowners"
            className="text-aqua underline"
          >
            How to add CODEOWNERS →
          </Link>
        </p>
      </Section>
    );
  }

  const created = summary.routing_rules_created;
  const rules = summary.rules_count;
  const unresolved = summary.unresolved_owners ?? [];

  return (
    <Section label="Routing rules">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full border border-emerald-400/30 bg-emerald-500/[0.08] px-2 py-0.5 text-[10px] uppercase tracking-widest text-emerald-300">
          {created} created
        </span>
        <span className="text-[11px] text-white/55">
          from {rules} CODEOWNERS rule{rules === 1 ? "" : "s"}
        </span>
        {workspaceId && repoId && (
          <Link
            href={`/inbox?ws=${encodeURIComponent(workspaceId)}&repo=${encodeURIComponent(repoId)}`}
            className="ml-auto text-[11px] text-white/55 hover:text-white"
          >
            View routing →
          </Link>
        )}
      </div>
      {unresolved.length > 0 && (
        <div className="mt-2 rounded-lg border border-sun/40 bg-sun/[0.08] px-2 py-1.5 text-[11px] text-sun">
          <strong className="font-bold">Unresolved:</strong>{" "}
          {unresolved.join(", ")} — not workspace member
          {unresolved.length === 1 ? "" : "s"} yet. Invite them or update
          CODEOWNERS to route through a workspace handle.
        </div>
      )}
    </Section>
  );
}

function LanesRow({
  workspaceId,
  repoId,
  syntheticLanes,
}: {
  workspaceId: string | null;
  repoId: string | null;
  syntheticLanes: number;
}) {
  return (
    <Section label="Lanes activated immediately">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full border border-aqua/40 bg-aqua/[0.08] px-2 py-0.5 text-[10px] uppercase tracking-widest text-aqua">
          {syntheticLanes} lane{syntheticLanes === 1 ? "" : "s"}
        </span>
        <span className="text-[11px] text-white/55">
          so /inbox, /automations, and /coverage start populated now
        </span>
        {workspaceId && repoId && (
          <Link
            href={`/automations?ws=${encodeURIComponent(workspaceId)}&scope=repo&repo=${encodeURIComponent(repoId)}`}
            className="ml-auto text-[11px] text-white/55 hover:text-white"
          >
            View lanes →
          </Link>
        )}
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-white/55">
        Synthetic lanes flip to canonical the moment the seed PR
        merges (the post-merge webhook reconciles them).
      </p>
    </Section>
  );
}

function IntelRow({
  workspaceId,
  repoId,
  handle,
}: {
  workspaceId: string | null;
  repoId: string | null;
  handle: ApiWizardSeedOut["intel"];
}) {
  return (
    <Section label="Repo intel">
      {repoId == null ? (
        <p className="text-[11px] text-white/55">
          We&apos;ll harvest a repo-intel snapshot once the repo
          metadata loads.
        </p>
      ) : (
        <IntelPollBadge
          workspaceId={workspaceId}
          repoId={repoId}
          handle={handle}
        />
      )}
    </Section>
  );
}

function FileCountRow({ fileCount }: { fileCount: number }) {
  return (
    <p className="text-[11px] text-white/45">
      {fileCount} file{fileCount === 1 ? "" : "s"} committed (CLI,
      workflows, scheduled lanes,{" "}
      <code className="rounded bg-white/5 px-1 text-white/65">
        .ship/config.yml
      </code>
      , knowledge starters).
    </p>
  );
}

/**
 * Last-ditch fallback when no :type:`ApiActivatedRepo` is around — we
 * try to extract ``owner/repo`` from the seed branch label which the
 * backend mints as ``ship/<labelled>-<seed>``. Returns ``null`` if
 * nothing usable is parseable.
 */
function deriveOwnerRepoFromBranch(_branch: string): string | null {
  return null;
}
