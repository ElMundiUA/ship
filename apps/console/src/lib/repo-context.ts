/**
 * Server-side resolver for repo-mode pages under
 * ``/r/[owner]/[repo]/…``.
 *
 * Every repo-scoped page performs the same dance:
 *
 *   1. Read the current session token; if missing → redirect to login.
 *   2. Fetch the caller's workspaces; pick one via ``?ws=`` or the
 *      persisted ``ship.ws`` cookie, else the first list entry.
 *   3. List activated repos and match by ``owner/repo`` slug. If the
 *      repo isn't there, the user either mistyped or the repo was
 *      decommissioned — produce a 404.
 *
 * Centralising this keeps every ``r/[owner]/[repo]/<section>/page.tsx``
 * free of boilerplate and guarantees consistent error handling (401,
 * backend-down, empty workspace) across the repo-mode surface.
 */

import "server-only";

import {
  ApiHttpError,
  ApiUnavailableError,
  listActivatedRepos,
  listWorkspaces,
  type ApiActivatedRepo,
} from "@/lib/api/client";
import type { ApiWorkspace } from "@/lib/api/types";
import { findRepoBySlug } from "@/lib/repo-slug";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";

export type RepoContext = {
  token: string;
  workspace: ApiWorkspace;
  allWorkspaces: ApiWorkspace[];
  repo: ApiActivatedRepo;
  repos: ApiActivatedRepo[];
};

export type RepoContextResult =
  | { kind: "ok"; ctx: RepoContext }
  | { kind: "unauthorized" }
  | { kind: "down" }
  | { kind: "empty" }
  | { kind: "not-found" };

/**
 * Resolve the repo the URL points at without throwing. The caller
 * decides how to react — most pages bounce to ``/login`` on
 * ``unauthorized``, render a "backend unreachable" card on ``down``,
 * redirect to onboarding on ``empty``, and call ``notFound()`` on
 * ``not-found``.
 */
export async function resolveRepoContext(
  token: string,
  slug: string,
  /** Current URL search params; ``?ws=`` and persisted cookie are merged. */
  searchParams: Record<string, string | string[] | undefined> | undefined,
): Promise<RepoContextResult> {
  let workspaces: ApiWorkspace[];
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return { kind: "unauthorized" };
    }
    if (err instanceof ApiUnavailableError) return { kind: "down" };
    return { kind: "down" };
  }
  if (workspaces.length === 0) return { kind: "empty" };
  const resolvedWs = await getResolvedWorkspaceId(
    searchParams ?? {},
    workspaces,
  );
  const workspace = pickWorkspace(workspaces, resolvedWs);

  let repos: ApiActivatedRepo[];
  try {
    repos = await listActivatedRepos(workspace.id, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return { kind: "unauthorized" };
    }
    if (err instanceof ApiUnavailableError) return { kind: "down" };
    return { kind: "down" };
  }
  const repo = findRepoBySlug(repos, slug);
  if (!repo) return { kind: "not-found" };

  return {
    kind: "ok",
    ctx: { token, workspace, allWorkspaces: workspaces, repo, repos },
  };
}
