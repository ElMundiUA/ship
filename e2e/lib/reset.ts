import type { APIRequestContext } from "@playwright/test";

import { GH_API, ghHeaders, parseRepo } from "./github-rest";
import { shipApiGet } from "./ship-api";

/**
 * Rollback helpers so the full-journey e2e is re-runnable against the same
 * sandbox repo + Ship workspace without manual cleanup between runs.
 *
 * Two surfaces are reset:
 *
 *   1. GitHub sandbox repo — closes `[e2e]` issues, closes PRs authored by
 *      the Ship bot ("chore: install Ship workflow"), optionally deletes
 *      `ship/*` branches. No force-push, no history rewrite: if you want
 *      the repo contents themselves back to a known baseline, tag the
 *      initial state as `e2e-baseline` and point ops at the README command
 *      (see e2e/README.md — "Reset sandbox repo to baseline").
 *
 *   2. Ship workspace — unwires activated repos and removes integrations
 *      (tracker + any others) via the authenticated `/v1/*` API. Pipelines
 *      and workspace rows are left in place so subsequent runs reuse the
 *      same tenant id without paying JIT-onboarding cost.
 *
 * Everything is idempotent — missing issues / already-closed PRs / absent
 * integrations all resolve to no-ops.
 */

// ---------------------------------------------------------------------------
// GitHub side
// ---------------------------------------------------------------------------

const SHIP_BRANCH_PREFIXES = ["ship/", "chore/ship-install-"] as const;
const SHIP_PR_TITLE_HINTS = [
  /install ship workflow/i,
  /ship[- :]install/i,
  /^ship:/i,
];

type GhIssue = { number: number; pull_request?: unknown; title?: string };
type GhPull = {
  number: number;
  title?: string;
  head?: { ref?: string };
  state?: string;
};

async function ghList<T>(
  request: APIRequestContext,
  token: string,
  path: string,
): Promise<T[]> {
  const res = await request.get(`${GH_API}${path}`, { headers: ghHeaders(token) });
  if (!res.ok()) return [];
  const body = (await res.json()) as unknown;
  return Array.isArray(body) ? (body as T[]) : [];
}

export interface GithubResetReport {
  closedIssues: number[];
  closedPullRequests: number[];
  deletedBranches: string[];
  errors: string[];
}

/**
 * Close `[e2e]`-tagged or `ship:*`-labelled issues, close Ship bot PRs,
 * optionally delete `ship/*` branches. Never touches the default branch
 * or anything authored by a human.
 */
export async function resetGithubSandboxRepo(
  request: APIRequestContext,
  opts: {
    repo: string;
    token: string;
    deleteBranches?: boolean;
    extraIssueTitleMarkers?: RegExp[];
  },
): Promise<GithubResetReport> {
  const parsed = parseRepo(opts.repo);
  if (!parsed) {
    return {
      closedIssues: [],
      closedPullRequests: [],
      deletedBranches: [],
      errors: [`invalid repo slug: ${opts.repo}`],
    };
  }
  const { owner, repo } = parsed;
  const report: GithubResetReport = {
    closedIssues: [],
    closedPullRequests: [],
    deletedBranches: [],
    errors: [],
  };
  const headers = ghHeaders(opts.token);
  const markers = [/\[e2e\]/i, ...(opts.extraIssueTitleMarkers ?? [])];

  // Close e2e-tagged or ship-labelled issues (GitHub's /issues returns PRs
  // too; filter those out via `pull_request`).
  const issues = await ghList<GhIssue>(
    request,
    opts.token,
    `/repos/${owner}/${repo}/issues?state=open&per_page=100&labels=ship:needs-clarification`,
  );
  const extraOpen = await ghList<GhIssue>(
    request,
    opts.token,
    `/repos/${owner}/${repo}/issues?state=open&per_page=100`,
  );
  const seen = new Set<number>();
  for (const it of [...issues, ...extraOpen]) {
    if (it.pull_request) continue;
    if (seen.has(it.number)) continue;
    seen.add(it.number);
    const match = markers.some((rx) => rx.test(it.title ?? ""));
    const labelled = issues.some((x) => x.number === it.number);
    if (!match && !labelled) continue;
    const r = await request.patch(
      `${GH_API}/repos/${owner}/${repo}/issues/${it.number}`,
      { headers, data: JSON.stringify({ state: "closed" }) },
    );
    if (r.ok()) report.closedIssues.push(it.number);
    else report.errors.push(`close issue #${it.number} → ${r.status()}`);
  }

  // Close Ship-authored PRs.
  const pulls = await ghList<GhPull>(
    request,
    opts.token,
    `/repos/${owner}/${repo}/pulls?state=open&per_page=100`,
  );
  for (const pr of pulls) {
    const title = pr.title ?? "";
    const head = pr.head?.ref ?? "";
    const matches =
      SHIP_PR_TITLE_HINTS.some((rx) => rx.test(title)) ||
      SHIP_BRANCH_PREFIXES.some((p) => head.startsWith(p));
    if (!matches) continue;
    const r = await request.patch(
      `${GH_API}/repos/${owner}/${repo}/pulls/${pr.number}`,
      { headers, data: JSON.stringify({ state: "closed" }) },
    );
    if (r.ok()) report.closedPullRequests.push(pr.number);
    else report.errors.push(`close PR #${pr.number} → ${r.status()}`);
  }

  if (opts.deleteBranches) {
    type Ref = { ref: string };
    const refs = await ghList<Ref>(
      request,
      opts.token,
      `/repos/${owner}/${repo}/git/matching-refs/heads/ship/`,
    );
    for (const r of refs) {
      const ref = r.ref.startsWith("refs/") ? r.ref.slice("refs/".length) : r.ref;
      const del = await request.delete(
        `${GH_API}/repos/${owner}/${repo}/git/refs/${ref}`,
        { headers },
      );
      if (del.ok() || del.status() === 422) {
        report.deletedBranches.push(ref);
      } else {
        report.errors.push(`delete ref ${ref} → ${del.status()}`);
      }
    }
  }

  return report;
}

// ---------------------------------------------------------------------------
// Ship API side
// ---------------------------------------------------------------------------

const DEFAULT_TRACKER_KINDS = ["github", "linear", "notion"] as const;

export interface WorkspaceResetReport {
  disconnectedRepos: string[];
  removedIntegrations: string[];
  errors: string[];
}

/**
 * Uses Ship admin Bearer (`E2E_SHIP_API_TOKEN`) to unwire the workspace:
 * DELETE activated repos + every tracker integration listed in
 * {@link DEFAULT_TRACKER_KINDS}. Leaves the workspace row itself alone so
 * the next run reuses the same JIT tenant.
 *
 * By default **all** activated repos are disconnected — assumes the
 * workspace is dedicated to e2e. If the same workspace also has non-
 * sandbox repos (common when the sandbox lives next to the team's
 * real work), pass ``onlyRepoFullName`` so the reset only touches the
 * sandbox row and leaves production repos wired up.
 */
export async function resetShipWorkspace(
  request: APIRequestContext,
  opts: {
    base: string;
    token: string;
    workspaceId: string;
    trackerKinds?: readonly string[];
    onlyRepoFullName?: string;
  },
): Promise<WorkspaceResetReport> {
  const report: WorkspaceResetReport = {
    disconnectedRepos: [],
    removedIntegrations: [],
    errors: [],
  };
  const base = opts.base.replace(/\/+$/, "");
  const wsEnc = encodeURIComponent(opts.workspaceId);
  const authHeaders = {
    Authorization: `Bearer ${opts.token}`,
    Accept: "application/json",
  };

  // 1. Disconnect repos.
  const listRes = await shipApiGet(request, `/v1/workspaces/${wsEnc}/repos`);
  if (listRes.ok()) {
    const rows = (await listRes.json()) as { id: string; full_name?: string }[];
    const filtered = opts.onlyRepoFullName
      ? rows.filter((r) => r.full_name === opts.onlyRepoFullName)
      : rows;
    for (const r of filtered) {
      const del = await request.delete(
        `${base}/v1/workspaces/${wsEnc}/repos/${encodeURIComponent(r.id)}`,
        { headers: authHeaders },
      );
      if (del.ok() || del.status() === 404) {
        report.disconnectedRepos.push(r.full_name ?? r.id);
      } else {
        report.errors.push(
          `disconnect repo ${r.full_name ?? r.id} → ${del.status()}`,
        );
      }
    }
  } else {
    report.errors.push(`list repos → ${listRes.status()}`);
  }

  // 2. Remove tracker integrations (404 is fine — not connected).
  for (const kind of opts.trackerKinds ?? DEFAULT_TRACKER_KINDS) {
    const del = await request.delete(
      `${base}/v1/workspaces/${wsEnc}/integrations/${encodeURIComponent(kind)}`,
      { headers: authHeaders },
    );
    if (del.ok() || del.status() === 404 || del.status() === 204) {
      report.removedIntegrations.push(kind);
    } else {
      report.errors.push(`integration ${kind} → ${del.status()}`);
    }
  }

  return report;
}
