import Link from "next/link";

import type { ApiActivatedRepo, ApiDashboard } from "@/lib/api/client";
import type { ApiWorkspace } from "@/lib/api/types";
import { repoBasePath } from "@/lib/repo-slug";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  StatTile,
} from "@/components/ui";

/**
 * Phase-1 workspace home — the landing for ``/``.
 *
 * The user rejected "aggregated dashboard of per-repo runs" at the
 * workspace level, so this page is deliberately NOT another mirror
 * of ``DashboardLive``. It lists the activated repos as navigable
 * channels (GitHub-team + Slack-channel shape) and advertises the
 * four workspace-unique primitives that land over the next few PRs
 * (Fleet Requests · Policy · Adoption · Knowledge graph).
 *
 * Counters at the top are workspace-wide rollups — repo count,
 * pipelines, PRs, runs — not tab-replacing metrics. They exist so a
 * freshly-landed operator can tell at a glance "fleet is healthy"
 * before picking a channel.
 */

export type WorkspaceHomeProps = {
  workspace: ApiWorkspace;
  repos: ApiActivatedRepo[];
  /** Optional workspace-wide dashboard summary; omitted = hide KPIs. */
  summary?: ApiDashboard | null;
};

const FLEET_TILES: {
  href: string;
  label: string;
  shipsIn: string;
  body: string;
}[] = [
  {
    href: "/runs?scope=fleet",
    label: "Fleet runs",
    shipsIn: "PR-2",
    body: "Fan one catalog pattern out across many repos in a single click.",
  },
  {
    href: "/automations?scope=fleet",
    label: "Fleet automations",
    shipsIn: "PR-5",
    body: "Cross-repo rules + mirror patterns enforced at the workspace level.",
  },
  {
    href: "/settings/policy",
    label: "Policy",
    shipsIn: "PR-5",
    body: "Standing rules ('Always work via PR') injected into agent instructions.",
  },
  {
    href: "/automations?tab=coverage",
    label: "Adoption",
    shipsIn: "PR-3",
    body: "Rollout funnel from installed → activated → steady state.",
  },
  {
    href: "/fleet/knowledge",
    label: "Knowledge graph",
    shipsIn: "PR-7",
    body: "Cross-repo runbook propagation + staleness detection.",
  },
];

export function WorkspaceHome({ workspace, repos, summary }: WorkspaceHomeProps) {
  const sortedRepos = [...repos].sort((a, b) =>
    a.full_name.localeCompare(b.full_name),
  );

  return (
    <>
      {summary && (
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            label="Active repos"
            value={summary.counts.active_repos.toString()}
            hint={`${repos.length} activated`}
          />
          <StatTile
            label="Enabled pipelines"
            value={summary.counts.enabled_pipelines.toString()}
            hint="across workspace"
          />
          <StatTile
            label="Open PRs"
            value={summary.counts.open_pull_requests.toString()}
            hint="tracked by Ship"
          />
          <StatTile
            label="Runs (24h)"
            value={summary.counts.runs_last_24h.toString()}
            hint="scheduled + event"
          />
        </section>
      )}

      <section className="mt-6">
        <div className="mb-3 flex items-end justify-between">
          <div>
            <h2 className="font-display text-lg font-bold text-white">
              Repos
            </h2>
            <p className="mt-0.5 text-xs text-white/55">
              {sortedRepos.length === 0
                ? "No repos activated yet."
                : `Open a repo to reach Lanes, Requests, Clarifications and the rest of the per-repo surfaces.`}
            </p>
          </div>
          <Link
            href={`/onboarding?step=repos&ws=${encodeURIComponent(workspace.id)}`}
            className="text-xs font-semibold text-aqua hover:underline"
          >
            + Activate repos →
          </Link>
        </div>
        {sortedRepos.length === 0 ? (
          <EmptyReposCard workspaceId={workspace.id} />
        ) : (
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {sortedRepos.map((repo) => (
              <RepoChannelCard key={repo.id} repo={repo} />
            ))}
          </ul>
        )}
      </section>

      <section className="mt-8">
        <div className="mb-3">
          <h2 className="font-display text-lg font-bold text-white">Fleet</h2>
          <p className="mt-0.5 text-xs text-white/55">
            Workspace-unique primitives — not mirrors of per-repo pages.
          </p>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {FLEET_TILES.map((tile) => (
            <Link
              key={tile.href}
              href={tile.href}
              className="group rounded-xl border border-white/10 bg-white/[0.03] p-4 transition hover:border-white/25 hover:bg-white/[0.06]"
            >
              <div className="mb-2 flex items-center gap-2">
                <Badge tone="info">{tile.label}</Badge>
                <Badge tone="neutral">Ships in {tile.shipsIn}</Badge>
                <span className="ml-auto text-white/30 transition group-hover:text-white">
                  →
                </span>
              </div>
              <p className="text-xs leading-relaxed text-white/65">{tile.body}</p>
            </Link>
          ))}
        </div>
      </section>
    </>
  );
}

function RepoChannelCard({ repo }: { repo: ApiActivatedRepo }) {
  const [owner, ...rest] = repo.full_name.split("/");
  const name = rest.join("/") || repo.full_name;
  const href = repoBasePath(repo);
  const installedBundle = repo.installed_bundle_version;
  const currentBundle = repo.current_bundle_version;
  const bundleOutdated =
    installedBundle !== null && installedBundle < currentBundle;

  return (
    <li>
      <Link
        href={href}
        data-testid="repo-channel"
        className="group flex h-full flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.03] p-4 transition hover:border-white/25 hover:bg-white/[0.06]"
      >
        <div className="flex items-center gap-2">
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-gradient-to-br from-lilac via-aqua to-coral text-[10px] font-bold text-ink">
            {(owner || name).slice(0, 2).toUpperCase()}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[10px] font-semibold uppercase tracking-widest text-white/45">
              {owner || "—"}
            </div>
            <div className="truncate text-sm font-semibold text-white">
              {name}
            </div>
          </div>
          <span className="text-white/30 transition group-hover:text-white">
            →
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[10px] uppercase tracking-widest">
          <Badge tone={repo.private ? "neutral" : "info"}>
            {repo.private ? "private" : "public"}
          </Badge>
          {repo.preset && <Badge tone="info">{repo.preset}</Badge>}
          {bundleOutdated && (
            <Badge tone="warn">update to v{currentBundle}</Badge>
          )}
          {installedBundle == null && (
            <Badge tone="warn">never seeded</Badge>
          )}
        </div>
      </Link>
    </li>
  );
}

function EmptyReposCard({ workspaceId }: { workspaceId: string }) {
  return (
    <Card>
      <CardHeader
        title="No repos activated yet"
        subtitle="Pick a repo to wire up Lanes, Requests, Clarifications and the rest."
      />
      <div className="flex flex-wrap gap-2">
        <ButtonPrimary>
          <Link
            href={`/onboarding?step=repos&ws=${encodeURIComponent(workspaceId)}`}
          >
            Activate a repo
          </Link>
        </ButtonPrimary>
        <ButtonGhost>
          <Link href="/integrations">Check integrations</Link>
        </ButtonGhost>
      </div>
    </Card>
  );
}
