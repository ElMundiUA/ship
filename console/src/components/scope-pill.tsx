"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";

/**
 * Phase 4: scope pill for the AppShell header.
 *
 * Surfaces the scope-ladder (workspace ≺ repo ⊕ user) the Phase 3
 * resolver exposes, so pages like ``/knowledge`` can filter their
 * content without every page rolling its own "which repo?" selector.
 * Project scope lands as a follow-up when the backend projects API
 * ships — the pill reads it from the URL already so bookmarks remain
 * valid the moment the option flips from hidden to visible.
 *
 * State shape (URL-driven):
 *   ?scope=workspace|repo|user[&repo_id=<uuid>][&project_id=<uuid>]
 *
 * Why URL-over-context:
 *   - Server Components (e.g. ``/knowledge/page.tsx``) can read it
 *     from ``searchParams`` without any client hydration dance.
 *   - Bookmarks / shared links keep the scope.
 *   - Back/forward in the browser "just works".
 *   - No need for a workspace-wide provider that every page would
 *     otherwise have to thread props through.
 *
 * The pill itself is a pure client component; it calls the router
 * to push URL updates. When the user picks "workspace" it clears
 * ``scope`` + ``repo_id`` / ``project_id`` from the URL so the page
 * path becomes canonical again (no stale query leftovers).
 */

export type ScopePillRepo = {
  id: string;
  full_name: string;
};

export type ScopePillUser = {
  id: string;
  email: string;
  display_name: string | null;
};

type ScopeKind = "workspace" | "repo" | "user";

const SCOPE_QS = "scope";
const REPO_QS = "repo_id";
const PROJECT_QS = "project_id";

function kindOf(raw: string | null): ScopeKind {
  if (raw === "repo" || raw === "user") return raw;
  return "workspace";
}

export function ScopePill({
  workspaceName,
  repos,
  me,
}: {
  workspaceName: string;
  repos: ScopePillRepo[];
  me?: ScopePillUser | null;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const kind = kindOf(params.get(SCOPE_QS));
  const repoId = params.get(REPO_QS);
  const selectedRepo = useMemo(
    () => repos.find((r) => r.id === repoId) ?? null,
    [repos, repoId],
  );

  // If the URL claims ``scope=repo`` but the repo_id no longer maps
  // to an activated repo (e.g. the user deactivated it), fall back
  // to workspace silently so the page below doesn't render empty
  // state forever. This also covers the "repo was never selected"
  // edge case (``?scope=repo`` with no ``repo_id``).
  const effectiveKind: ScopeKind =
    kind === "repo" && !selectedRepo ? "workspace" : kind;

  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close on outside click. We deliberately don't use a backdrop so
  // the rest of the UI (scroll, clicks on links) stays live.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (!containerRef.current) return;
      if (!containerRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  function pushScope(next: {
    kind: ScopeKind;
    repoId?: string | null;
    projectId?: string | null;
  }) {
    const qs = new URLSearchParams(params.toString());
    if (next.kind === "workspace") {
      qs.delete(SCOPE_QS);
      qs.delete(REPO_QS);
      qs.delete(PROJECT_QS);
    } else {
      qs.set(SCOPE_QS, next.kind);
      if (next.kind === "repo") {
        if (next.repoId) qs.set(REPO_QS, next.repoId);
        else qs.delete(REPO_QS);
        qs.delete(PROJECT_QS);
      } else if (next.kind === "user") {
        qs.delete(REPO_QS);
        qs.delete(PROJECT_QS);
      }
    }
    const suffix = qs.toString();
    router.push(suffix ? `${pathname}?${suffix}` : pathname);
    setOpen(false);
  }

  const label = labelFor(effectiveKind, {
    workspaceName,
    selectedRepo,
    me,
  });

  return (
    <div ref={containerRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((s) => !s)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Filter by scope"
        className={cn(
          "group inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] transition",
          open
            ? "border-lilac/60 bg-lilac/10 text-lilac"
            : "border-white/10 bg-white/[0.04] text-white/70 hover:border-white/20 hover:text-white",
        )}
        data-testid="scope-pill"
        data-scope={effectiveKind}
      >
        <ScopeGlyph kind={effectiveKind} />
        <span className="max-w-[20ch] truncate normal-case tracking-normal">
          {label.primary}
        </span>
        {label.secondary && (
          <span className="text-[8px] tracking-wider text-white/40 group-hover:text-white/70">
            {label.secondary}
          </span>
        )}
        <span className="ml-0.5 text-white/40">⌄</span>
      </button>

      {open && (
        <div
          className="absolute left-0 top-[calc(100%+6px)] z-50 w-72 overflow-hidden rounded-xl border border-white/10 bg-black/85 shadow-2xl backdrop-blur-xl"
          role="listbox"
        >
          <div className="border-b border-white/10 px-4 py-2 text-[10px] uppercase tracking-widest text-white/45">
            Scope
          </div>
          <ScopeOption
            active={effectiveKind === "workspace"}
            title={workspaceName}
            kicker="Workspace · shared"
            onClick={() => pushScope({ kind: "workspace" })}
          />
          <div className="border-t border-white/10 px-4 py-2 text-[10px] uppercase tracking-widest text-white/45">
            Repo
          </div>
          {repos.length === 0 ? (
            <div className="px-4 py-3 text-[11px] text-white/50">
              No activated repos in this workspace.{" "}
              <Link
                href="/onboarding?step=repos"
                className="font-semibold text-aqua hover:underline"
              >
                Activate one →
              </Link>
            </div>
          ) : (
            repos.map((r) => {
              const active =
                effectiveKind === "repo" && selectedRepo?.id === r.id;
              return (
                <ScopeOption
                  key={r.id}
                  active={active}
                  title={r.full_name.split("/").slice(1).join("/") || r.full_name}
                  kicker={r.full_name}
                  onClick={() =>
                    pushScope({ kind: "repo", repoId: r.id })
                  }
                />
              );
            })
          )}
          {me && (
            <>
              <div className="border-t border-white/10 px-4 py-2 text-[10px] uppercase tracking-widest text-white/45">
                Me
              </div>
              <ScopeOption
                active={effectiveKind === "user"}
                title={me.display_name || me.email}
                kicker="User · private overlay"
                onClick={() => pushScope({ kind: "user" })}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ScopeOption({
  active,
  title,
  kicker,
  onClick,
}: {
  active: boolean;
  title: string;
  kicker: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "flex w-full items-start gap-2 border-b border-white/5 px-4 py-2.5 text-left text-xs transition last:border-b-0",
        active
          ? "bg-white/[0.06] text-white"
          : "text-white/70 hover:bg-white/[0.04] hover:text-white",
      )}
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-semibold text-white">
          {title}
        </span>
        <span className="block truncate text-[10px] uppercase tracking-widest text-white/45">
          {kicker}
        </span>
      </span>
      {active && <span className="pt-1 text-lilac">●</span>}
    </button>
  );
}

function ScopeGlyph({ kind }: { kind: ScopeKind }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid h-4 w-4 shrink-0 place-items-center rounded-sm text-[8px] font-bold text-ink",
        kind === "workspace" && "bg-gradient-to-br from-aqua via-lilac to-coral",
        kind === "repo" && "bg-gradient-to-br from-aqua to-lilac",
        kind === "user" && "bg-gradient-to-br from-coral to-lilac",
      )}
    >
      {kind === "workspace" ? "W" : kind === "repo" ? "R" : "U"}
    </span>
  );
}

function labelFor(
  kind: ScopeKind,
  ctx: {
    workspaceName: string;
    selectedRepo: ScopePillRepo | null;
    me?: ScopePillUser | null;
  },
): { primary: string; secondary?: string } {
  if (kind === "repo" && ctx.selectedRepo) {
    const parts = ctx.selectedRepo.full_name.split("/", 2);
    return {
      primary:
        ctx.selectedRepo.full_name.split("/").slice(1).join("/") ||
        ctx.selectedRepo.full_name,
      secondary: parts[0] || undefined,
    };
  }
  if (kind === "user" && ctx.me) {
    return {
      primary: ctx.me.display_name || ctx.me.email,
      secondary: "me",
    };
  }
  return { primary: ctx.workspaceName, secondary: "ws" };
}

// Reading helpers for Server Components that need to mirror the pill
// state server-side. Kept in the same file as the pill so the URL
// contract has one source of truth.

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
