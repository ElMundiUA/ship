/**
 * Server-safe helpers for the Phase 4 scope pill URL contract.
 *
 * ``scope-pill.tsx`` itself is a ``"use client"`` module — it hooks
 * into ``useRouter`` / ``useSearchParams`` to push URL updates. Next
 * 15.5 hardened the rule that non-component exports from a client
 * module cannot be invoked from a Server Component: doing so throws
 * the infamous ``Attempted to call X() from the server but X is on
 * the client``. Our scope-aware feature pages (``/chat``, ``/lanes``,
 * ``/knowledge``, ``/clarifications``, ``/improvements``) are all
 * Server Components that need to mirror the pill state in their
 * SSR data load, so the pure reader lives here instead — no React
 * imports, no client-only hooks, safe to tree-shake either way.
 *
 * The URL shape is the same one the pill writes:
 *   ?scope=workspace|repo|user[&repo_id=<uuid>][&project_id=<uuid>]
 */

export type ScopeKind = "workspace" | "repo" | "user";

export type ResolvedScope = {
  kind: ScopeKind;
  repoId: string | null;
  projectId: string | null;
};

export function resolveScopeFromSearch(params: {
  scope?: string | string[];
  repo_id?: string | string[];
  project_id?: string | string[];
}): ResolvedScope {
  const scope = firstOf(params.scope);
  const kind: ScopeKind =
    scope === "repo" || scope === "user" ? scope : "workspace";
  const repoId = kind === "repo" ? firstOf(params.repo_id) ?? null : null;
  const projectId = kind === "repo" ? firstOf(params.project_id) ?? null : null;
  return { kind, repoId, projectId };
}

function firstOf(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}
