/**
 * Tracker FSM settings page (Wizard v2 iter 7).
 *
 * Read-only mirror of ``.ship/tracker-fsm.md`` — the file the wizard
 * seed PR drops into every repo. Lets operators:
 *
 *  - See the canonical Ship state machine (``triage → ready →
 *    in_progress → …``) alongside the per-tracker mapping hints
 *    (Linear / GitHub / Jira).
 *  - See, for each activated repo, which tracker kind it resolves
 *    to (per-repo override, workspace default, or unbound) and the
 *    exact markdown body Ship would (re)seed today.
 *  - Preview the rendered markdown so they know what the seed PR
 *    actually carries without leaving the console.
 *
 * The markdown file in the repo is still the source of truth — this
 * endpoint just *renders* the canonical spec. If the team customised
 * the file in-repo, ``shipctl run`` picks up the committed version
 * verbatim; the preview here reflects what a fresh seed would write.
 */

import Link from "next/link";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader, LiveBanner, MockBanner } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  getTrackerFsm,
  isApiConfigured,
  listWorkspaces,
  type ApiRepoFsm,
  type ApiTrackerFsm,
} from "@/lib/api/client";
import type { ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";

export const dynamic = "force-dynamic";

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      fsm: ApiTrackerFsm;
    }
  | { source: "mock"; reason: string };

const TRACKER_LABELS: Record<string, string> = {
  linear: "Linear",
  github: "GitHub Issues",
  jira: "Jira",
};

async function load(): Promise<Mode> {
  if (!isApiConfigured()) {
    return { source: "mock", reason: "SHIP_API_URL not set" };
  }
  const token = await getSessionToken();
  if (!token) {
    return { source: "mock", reason: "Sign in to view the tracker FSM" };
  }
  try {
    const workspaces = await listWorkspaces(token);
    if (workspaces.length === 0) {
      return {
        source: "mock",
        reason: "Create a workspace first to see the FSM spec",
      };
    }
    const workspace = workspaces[0];
    const fsm = await getTrackerFsm(workspace.id, { token });
    return { source: "live", workspace, fsm };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return { source: "mock", reason: "Session expired — sign in again" };
    }
    if (err instanceof ApiHttpError && err.status === 404) {
      return {
        source: "mock",
        reason:
          "Workspace not found. Accept an invite or ping your workspace owner.",
      };
    }
    if (err instanceof ApiUnavailableError) {
      return { source: "mock", reason: "Backend unreachable" };
    }
    return { source: "mock", reason: "Backend returned an error" };
  }
}

export default async function TrackerFsmPage() {
  const data = await load();
  if (data.source === "mock") {
    return <MockView reason={data.reason} />;
  }
  const { workspace, fsm } = data;
  return (
    <AppShell
      kicker={`${workspace.name} · tracker FSM`}
      title="Ticket lifecycle"
      actions={
        <Link
          href="/settings"
          className="rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-bold text-white/85 transition hover:bg-white/[0.08]"
        >
          Back to settings
        </Link>
      }
    >
      <LiveBanner workspace={workspace.slug} />

      {/* ── Summary ─────────────────────────────────────────────── */}
      <Card padded>
        <CardHeader
          title="How Ship drives tickets"
          subtitle={
            <>
              Every Ship-driven ticket moves through the states below.
              Transitions here are the only ones Ship triggers autonomously
              — anything else (re-opening <code>done</code>, skipping
              straight to <code>merged</code>) needs an operator. The file
              on disk at{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                {fsm.install_path}
              </code>{" "}
              is the source of truth; edit it in-repo and{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                shipctl run
              </code>{" "}
              picks up the new spec on the next lane tick.
            </>
          }
        />
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-white/70">
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5">
            {fsm.states.length} states
          </span>
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5">
            {Object.keys(fsm.mapping_hints).length} tracker mapping
            {Object.keys(fsm.mapping_hints).length === 1 ? "" : "s"}
          </span>
          {fsm.workspace_default_kind ? (
            <span className="rounded-full border border-aqua/40 bg-aqua/10 px-2 py-0.5 text-aqua">
              Workspace default: {TRACKER_LABELS[fsm.workspace_default_kind] ?? fsm.workspace_default_kind}
            </span>
          ) : (
            <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-white/60">
              No workspace default yet
            </span>
          )}
        </div>
      </Card>

      {/* ── States grid ─────────────────────────────────────────── */}
      <section className="mt-6">
        <h2 className="text-[11px] font-bold uppercase tracking-widest text-white/55">
          Canonical states
        </h2>
        <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {fsm.states.map((state) => {
            const terminal = state.transitions.length === 0;
            return (
              <div
                key={state.id}
                className={
                  "rounded-2xl border p-4 shadow-card " +
                  (terminal
                    ? "border-white/15 bg-white/[0.03]"
                    : "border-aqua/25 bg-aqua/[0.04]")
                }
              >
                <div className="flex items-center gap-2">
                  <code className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[11px] tracking-wide text-aqua">
                    {state.id}
                  </code>
                  <span className="font-semibold text-white">
                    {state.label}
                  </span>
                  {terminal && (
                    <span className="ml-auto rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-white/55">
                      terminal
                    </span>
                  )}
                </div>
                <p className="mt-2 text-[12px] leading-relaxed text-white/70">
                  {state.description}
                </p>
                {state.transitions.length > 0 && (
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    <span className="text-[10px] uppercase tracking-widest text-white/45">
                      next
                    </span>
                    {state.transitions.map((t) => (
                      <code
                        key={t}
                        className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-white/75"
                      >
                        {t}
                      </code>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {/* ── Mapping table ──────────────────────────────────────── */}
      <section className="mt-8">
        <h2 className="text-[11px] font-bold uppercase tracking-widest text-white/55">
          Native-status mapping
        </h2>
        <p className="mt-1 text-[12px] leading-relaxed text-white/60">
          Ship targets these native statuses per tracker. Rename them on the
          tracker side if you prefer — just mirror the rename in{" "}
          <code className="rounded bg-white/5 px-1">{fsm.install_path}</code>{" "}
          so <code className="rounded bg-white/5 px-1">shipctl</code> keeps
          finding the right slot.
        </p>
        <Card padded={false} className="mt-3 overflow-hidden">
          <table className="min-w-full text-sm">
            <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">
                  Ship state
                </th>
                {Object.keys(fsm.mapping_hints).map((kind) => (
                  <th
                    key={kind}
                    className="px-4 py-2 text-left font-semibold"
                  >
                    {TRACKER_LABELS[kind] ?? kind}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {fsm.states.map((state) => (
                <tr key={state.id} className="hover:bg-white/[0.02]">
                  <td className="px-4 py-2 align-top">
                    <code className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[11px] text-aqua">
                      {state.id}
                    </code>{" "}
                    <span className="text-white/70">{state.label}</span>
                  </td>
                  {Object.entries(fsm.mapping_hints).map(([kind, mapping]) => (
                    <td
                      key={kind}
                      className="px-4 py-2 align-top text-[12px] text-white/80"
                    >
                      {mapping[state.id] ?? (
                        <span className="text-white/35">—</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </section>

      {/* ── Per-repo previews ─────────────────────────────────── */}
      <section className="mt-8">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-[11px] font-bold uppercase tracking-widest text-white/55">
            Per-repo rendering
          </h2>
          <span className="text-[11px] text-white/45">
            {fsm.repos.length} activated repo
            {fsm.repos.length === 1 ? "" : "s"}
          </span>
        </div>
        {fsm.repos.length === 0 ? (
          <div className="mt-3 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-6 text-center text-sm text-white/60">
            No activated repos yet. Finish the{" "}
            <Link
              href="/onboarding?step=configure"
              className="text-aqua underline-offset-2 hover:underline"
            >
              onboarding wizard
            </Link>{" "}
            to seed at least one repo.
          </div>
        ) : (
          <div className="mt-3 space-y-4">
            {fsm.repos.map((repo) => (
              <RepoPreview key={repo.repo_id} repo={repo} />
            ))}
          </div>
        )}
      </section>
    </AppShell>
  );
}

function RepoPreview({ repo }: { repo: ApiRepoFsm }) {
  const kindLabel = repo.tracker_kind
    ? TRACKER_LABELS[repo.tracker_kind] ?? repo.tracker_kind
    : null;
  return (
    <Card padded={false} className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-5 py-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-white">{repo.full_name}</h3>
          <div className="mt-0.5 flex items-center gap-2 text-[10px] uppercase tracking-widest text-white/50">
            {kindLabel ? (
              <>
                <span className="rounded bg-aqua/15 px-1.5 py-0.5 text-aqua">
                  {kindLabel}
                </span>
                <span>
                  {repo.source === "repo"
                    ? "per-repo override"
                    : repo.source === "workspace"
                      ? "inherits workspace default"
                      : ""}
                </span>
              </>
            ) : (
              <span className="rounded bg-coral/15 px-1.5 py-0.5 text-coral">
                no tracker bound
              </span>
            )}
          </div>
        </div>
      </div>
      <details className="px-5 py-4">
        <summary className="cursor-pointer text-xs font-bold uppercase tracking-widest text-white/55 hover:text-white">
          Preview rendered markdown
        </summary>
        <pre className="mt-3 max-h-[420px] overflow-auto rounded-xl border border-white/10 bg-black/30 p-4 font-mono text-[11px] leading-relaxed text-white/80">
          {repo.markdown}
        </pre>
      </details>
    </Card>
  );
}

function MockView({ reason }: { reason: string }) {
  return (
    <AppShell kicker="Tracker FSM" title="Ticket lifecycle">
      <MockBanner reason={reason} />
      <Card padded>
        <CardHeader
          title="The FSM is how Ship drives tickets"
          subtitle="Connect a workspace and a tracker via the onboarding wizard to see the canonical state machine, per-tracker mappings, and per-repo renderings here."
        />
        <div className="mt-3 flex flex-wrap gap-3">
          <Link
            href="/onboarding"
            className="rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-bold text-aqua transition hover:bg-aqua/[0.18]"
          >
            Start onboarding →
          </Link>
          <Link
            href="/settings"
            className="rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-bold text-white/85 transition hover:bg-white/[0.08]"
          >
            Back to settings
          </Link>
        </div>
      </Card>
    </AppShell>
  );
}
