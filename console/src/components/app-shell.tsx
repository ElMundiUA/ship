"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { type ReactNode, Suspense, useEffect, useState } from "react";
import { cn } from "@/lib/cn";
import { NavigatorLauncher } from "@/components/navigator-launcher";

/**
 * App shell for the in-app cloud platform console (separate Next.js app).
 *
 * Three columns:
 *   - left rail with workspace switcher + primary nav (sticky on desktop, drawer on mobile)
 *   - top bar with breadcrumb / page actions / user
 *   - main scrollable surface
 *
 * Intentionally separate from the public marketing chrome (`SiteHeader` /
 * `SiteFooter`) so we can iterate on the operator UX without breaking
 * any of the published landing pages.
 */

type NavItem = {
  href: string;
  label: string;
  icon: ReactNode;
  badge?: string;
  stub?: boolean;
};

type NavGroup = { section: string; items: NavItem[] };

// Pages that aren't backed by real `/v1` endpoints yet — kept around as
// rendered routes for design reference but hidden from the operator nav so
// the pilot tenant doesn't bump into "coming soon" walls. Toggle with
// NEXT_PUBLIC_SHIP_SHOW_STUBS=1 to surface them again during console
// development.
const SHOW_STUBS = process.env.NEXT_PUBLIC_SHIP_SHOW_STUBS === "1";

/** When set, sidebar shows a link (e.g. GitHub issues, Linear askevery). */
const SHIP_FEEDBACK_URL = process.env.NEXT_PUBLIC_SHIP_FEEDBACK_URL?.trim();
const SHIP_FEEDBACK_LABEL =
  process.env.NEXT_PUBLIC_SHIP_FEEDBACK_LABEL?.trim() || "Ship feedback";

/**
 * Phase-1 two-mode shell: the sidebar flips between a **workspace**
 * nav (``/`` + ``/fleet/*`` + workspace-wide configure) and a
 * **repo** nav (everything scoped to ``/r/<owner>/<repo>/...``).
 *
 * Rationale: legacy per-repo pages (Lanes, Requests, Pipelines,
 * Clarifications, …) do NOT belong at the workspace level — the
 * user explicitly rejected "union of all repos" pages. They live
 * under the repo segment and only appear in that sidebar. The
 * workspace level surfaces workspace-unique primitives (Fleet
 * Requests, Policy, Adoption, Knowledge Graph) plus cross-repo
 * configure pages (workspace settings, members, integrations,
 * audit log). No duplication between the two modes.
 *
 * Retired mock/reporting pages and pre-redesign top-level paths are no
 * longer kept around as standalone routes.
 */
function buildWorkspaceNav(): NavGroup[] {
  return [
    {
      section: "Workspace",
      items: [
        { href: "/", label: "Dashboard", icon: <DotIcon /> },
        { href: "/inbox", label: "Inbox", icon: <DotIcon /> },
        { href: "/process", label: "Process", icon: <DotIcon /> },
        { href: "/knowledge", label: "Knowledge", icon: <DotIcon /> },
        { href: "/settings/policy", label: "Policies", icon: <DotIcon /> },
        { href: "/audit", label: "Audit", icon: <DotIcon /> },
      ],
    },
  ];
}

function buildRepoNav(slugPath: string): NavGroup[] {
  const base = `/r/${slugPath}`;
  return [
    {
      section: "Operate",
      items: [
        { href: base, label: "Home", icon: <DotIcon /> },
        { href: `${base}/settings`, label: "Settings", icon: <DotIcon /> },
      ],
    },
  ];
}

/**
 * Extracts ``owner/repo`` from a ``/r/<owner>/<repo>[/...]`` pathname.
 * Returns ``null`` when not in repo mode. The ``[...slug]`` catch-all
 * stores at least two segments; anything shorter is treated as a
 * malformed repo URL (the layout will 404 separately).
 */
function parseRepoSlug(pathname: string | null): string | null {
  if (!pathname || !pathname.startsWith("/r/")) return null;
  const parts = pathname.slice(3).split("/").filter(Boolean);
  if (parts.length < 2) return null;
  return `${parts[0]}/${parts[1]}`;
}

function navFor(pathname: string | null): NavGroup[] {
  const slug = parseRepoSlug(pathname);
  const raw = slug ? buildRepoNav(slug) : buildWorkspaceNav();
  return raw
    .map((g) => ({
      section: g.section,
      items: SHOW_STUBS ? g.items : g.items.filter((i) => !i.stub),
    }))
    .filter((g) => g.items.length > 0);
}

function DotIcon() {
  return (
    <span
      aria-hidden
      className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-70"
    />
  );
}

function normalizePathname(p: string): string {
  if (p === "/") return "/";
  return p.endsWith("/") ? p.slice(0, -1) : p;
}

/**
 * Marks a nav link active when the URL matches that link or is nested
 * under it — but if several links match (e.g. repo ``/r/o/r`` and
 * ``/r/o/r/requests``), only the **longest** href wins so the repo "Home"
 * row does not stay highlighted on every subpage.
 */
function isNavItemActive(
  pathname: string | null,
  href: string,
  allNavHrefs: readonly string[],
): boolean {
  if (!pathname) return false;
  const pn = normalizePathname(pathname);
  const target = normalizePathname(href);

  if (target === "/") return pn === "/";

  const matches = allNavHrefs.filter((h) => {
    const candidate = normalizePathname(h);
    if (candidate === "/") return pn === "/";
    return pn === candidate || pn.startsWith(`${candidate}/`);
  });
  if (matches.length === 0) return false;
  const longest = matches.reduce((a, b) =>
    normalizePathname(a).length >= normalizePathname(b).length ? a : b,
  );
  return normalizePathname(longest) === target;
}

export type AppShellWorkspace = {
  id: string;
  name: string;
  slug: string;
};

export type AppShellRepo = {
  id: string;
  full_name: string;
};

export type AppShellScope = {
  repos: AppShellRepo[];
  selectedRepoId?: string | null;
};

export type AppShellUser = {
  name: string;
  email: string;
  initials: string;
};

export function AppShell({
  children,
  title,
  kicker,
  actions,
  workspace,
  allWorkspaces,
  scopePill,
  me,
}: {
  children: ReactNode;
  title: string;
  kicker?: string;
  actions?: ReactNode;
  workspace?: AppShellWorkspace;
  /**
   * When the signed-in user belongs to more than one workspace, pass the
   * full list so the header can switch ``?ws=`` and the sidebar preserves it.
   */
  allWorkspaces?: AppShellWorkspace[];
  scope?: AppShellScope;
  /**
   * Phase 4: optional scope filter for the header. Pages that care
   * about scope (e.g. ``/knowledge``) pass the pill pre-rendered
   * from their server component so it can pass in activated-repo
   * and current-user data fetched during SSR. Pages that don't
   * care leave this ``undefined`` and the header stays the same
   * single-chip shape it's always had.
   */
  scopePill?: ReactNode;
  /**
   * Currently signed-in operator, threaded down from the page
   * server component so the sidebar footer can display the real
   * email + initials instead of the mock fallback. Pages that
   * haven't been wired up yet (or surfaces that intentionally
   * render the marketing-style preview) leave this ``undefined``
   * and the mock ``currentUser`` shows through.
   */
  me?: AppShellUser | null;
}) {
  const pathname = usePathname();
  const [wsOpen, setWsOpen] = useState(false);
  const defaultWs = { name: "Workspace", org: "Organization" };
  const wsLabel = workspace?.name ?? defaultWs.name;
  const wsKicker = workspace?.slug ?? defaultWs.org;
  const multiWorkspace =
    Boolean(workspace?.id) &&
    Array.isArray(allWorkspaces) &&
    allWorkspaces.length > 1;
  const withWorkspaceHref = (href: string) => {
    if (!multiWorkspace || !workspace?.id) {
      return href;
    }
    const u = new URL(href, "http://local.workspace");
    u.searchParams.set("ws", workspace.id);
    return u.pathname + u.search;
  };
  // Pages that haven't threaded ``me`` down from server-side fall back
  // to a client-side fetch so the sidebar still shows the real signed-
  // in operator instead of the placeholder. The fallback is gated on
  // ``me === undefined`` so callers that explicitly pass ``null`` (no
  // session) keep showing the mock without an extra request.
  const [fetchedMe, setFetchedMe] = useState<AppShellUser | null>(null);
  useEffect(() => {
    if (me !== undefined) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/me", {
          headers: { accept: "application/json" },
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as {
          email?: string;
          display_name?: string | null;
        };
        if (cancelled) return;
        const display = (data.display_name ?? "").trim();
        const email = (data.email ?? "").trim();
        const name = display || email || "User";
        setFetchedMe({
          name,
          email,
          initials: initialsFromMe(display, email),
        });
      } catch {
        // Best-effort — fallback placeholder is fine if the call fails.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [me]);

  const userInfo = me ?? fetchedMe ?? {
    name: "User",
    email: "user@example.com",
    initials: "U",
  };
  const NAV = navFor(pathname);
  const allNavHrefs = NAV.flatMap((g) => g.items.map((i) => i.href));
  const repoSlug = parseRepoSlug(pathname);
  // Repo-mode header chip: show the repo the URL resolves to. In
  // workspace mode there is no "current repo" concept — the sidebar
  // is a list of workspace-level primitives, so we hide the block.
  const repoChip = repoSlug
    ? {
        slug: repoSlug,
        // In repo mode pages also pass ``scope`` with the resolved
        // repo id, but we only need the human-readable slug for the
        // chip. The id is used elsewhere (scope-pill, forms).
      }
    : null;

  return (
    <div className="min-h-screen bg-ink text-mist">
      <div className="flex w-full gap-0">
        {/* sidebar */}
        <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-white/10 bg-black/30 backdrop-blur-xl lg:flex">
          <div className="border-b border-white/10 px-4 py-5">
            <Link
              href={withWorkspaceHref("/")}
              className="font-display text-base font-bold tracking-tight text-white"
            >
              Ship<span className="text-aqua">.</span>
              <span className="ml-1 rounded-md border border-aqua/40 bg-aqua/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua/90">
                cloud
              </span>
            </Link>
          </div>

          <div className="relative border-b border-white/10 px-3 py-3">
            <WorkspaceChip
              label={wsLabel}
              kicker={wsKicker}
              open={wsOpen}
              onToggle={() => setWsOpen((s) => !s)}
              className="w-full justify-start rounded-xl px-3 py-2"
            />
            {wsOpen && (
              <Suspense
                fallback={
                  <div className="absolute left-3 right-3 top-[60px] z-50 overflow-hidden rounded-xl border border-white/10 bg-black/60 px-4 py-3 text-[11px] text-white/45">
                    Loading…
                  </div>
                }
              >
                <WorkspaceSwitcherMenu
                  pathname={pathname}
                  wsLabel={wsLabel}
                  workspace={workspace}
                  allWorkspaces={allWorkspaces}
                  multiWorkspace={multiWorkspace}
                  withWorkspaceHref={withWorkspaceHref}
                  onPick={() => setWsOpen(false)}
                />
              </Suspense>
            )}
          </div>

          {repoChip && (
            <div className="border-b border-white/10 px-3 py-3">
              <Link
                href={withWorkspaceHref("/")}
                className="mb-2 inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-widest text-white/45 hover:text-white"
                title="Back to workspace home"
              >
                <span aria-hidden>←</span>
                <span>Workspace</span>
              </Link>
              <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-2">
                <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-gradient-to-br from-lilac via-aqua to-coral text-[10px] font-bold text-ink">
                  {initialsOf(
                    repoChip.slug.split("/", 1)[0] ?? repoChip.slug,
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[10px] font-semibold uppercase tracking-widest text-white/45">
                    {repoChip.slug.split("/", 1)[0]}
                  </span>
                  <span className="block truncate text-sm font-semibold text-white">
                    {repoChip.slug.split("/").slice(1).join("/") ||
                      repoChip.slug}
                  </span>
                </span>
              </div>
            </div>
          )}

          <nav className="flex-1 overflow-y-auto px-2 py-3">
            {NAV.map((group) => (
              <div key={group.section} className="mb-4">
                <div className="px-3 pb-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-white/35">
                  {group.section}
                </div>
                <ul className="space-y-0.5">
                  {group.items.map((item) => {
                    const active = isNavItemActive(pathname, item.href, allNavHrefs);
                    return (
                      <li key={item.href}>
                        <Link
                          href={withWorkspaceHref(item.href)}
                          aria-current={active ? "page" : undefined}
                          className={cn(
                            "flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm transition",
                            active
                              ? "bg-white/[0.08] text-white shadow-[inset_2px_0_0_theme(colors.aqua)]"
                              : "text-white/65 hover:bg-white/[0.04] hover:text-white"
                          )}
                        >
                          <span className={active ? "text-aqua" : "text-white/50"}>{item.icon}</span>
                          <span className="flex-1 truncate">{item.label}</span>
                          {item.badge && (
                            <span className="rounded-full bg-coral/20 px-1.5 py-px text-[10px] font-bold text-coral">
                              {item.badge}
                            </span>
                          )}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>

          {SHIP_FEEDBACK_URL ? (
            <div className="border-t border-white/10 px-3 py-2">
              <a
                href={SHIP_FEEDBACK_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="block rounded-md px-2 py-1.5 text-[11px] font-semibold text-aqua/90 transition hover:bg-white/[0.04] hover:text-aqua"
              >
                {SHIP_FEEDBACK_LABEL}
                <span className="ml-1 text-white/45" aria-hidden>
                  ↗
                </span>
              </a>
              <p className="mt-0.5 px-2 text-[10px] leading-snug text-white/35">
                Bugs and product ideas for Ship itself.
              </p>
            </div>
          ) : null}

          <div className="border-t border-white/10 p-3">
            <div className="flex items-center gap-2 rounded-md px-2 py-1.5">
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-gradient-to-br from-aqua via-lilac to-coral text-[10px] font-bold text-ink">
                {userInfo.initials}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-semibold text-white">{userInfo.name}</div>
                <div className="truncate text-[10px] text-white/40">{userInfo.email}</div>
              </div>
              <Link
                href={withWorkspaceHref("/settings")}
                className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-xl text-white/55 transition hover:bg-white/10 hover:text-white"
                aria-label="Workspace settings"
                title="Workspace settings"
              >
                ⚙︎
              </Link>
              <form action="/logout" method="POST" className="contents">
                <button
                  type="submit"
                  className="rounded-md p-1 text-white/40 hover:bg-white/5 hover:text-white"
                  aria-label="Sign out"
                  title="Sign out"
                >
                  ⎋
                </button>
              </form>
            </div>
          </div>
        </aside>

        {/* main column */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-40 border-b border-white/10 bg-ink/80 backdrop-blur-xl">
            <div className="flex items-center gap-3 px-6 py-4 lg:px-8">
              <div className="relative min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  {scopePill}
                  {kicker && kicker !== wsKicker && (
                    <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-aqua/80">
                      {kicker}
                    </div>
                  )}
                </div>
                <h1 className="font-display mt-1 truncate text-xl font-bold leading-tight text-white sm:text-2xl">
                  {title}
                </h1>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <NavigatorLauncher />
                <div className="hidden items-center gap-2 md:flex">{actions}</div>
              </div>
            </div>
          </header>
          <main className="px-6 pb-16 pt-6 lg:px-8 lg:pb-20 lg:pt-8">{children}</main>
        </div>
      </div>
    </div>
  );
}

/**
 * Isolated so ``useSearchParams`` can be wrapped in ``Suspense`` (Next
 * static / prerender).
 */
function WorkspaceSwitcherMenu({
  pathname,
  wsLabel,
  workspace,
  allWorkspaces,
  multiWorkspace,
  withWorkspaceHref,
  onPick,
}: {
  pathname: string | null;
  wsLabel: string;
  workspace?: AppShellWorkspace;
  allWorkspaces?: AppShellWorkspace[];
  multiWorkspace: boolean;
  withWorkspaceHref: (href: string) => string;
  onPick: () => void;
}) {
  const searchParams = useSearchParams();
  const basePath = pathname ?? "/";
  const hrefSwitchWorkspace = (targetId: string) => {
    const sp = new URLSearchParams(searchParams.toString());
    sp.set("ws", targetId);
    const q = sp.toString();
    return q ? `${basePath}?${q}` : basePath;
  };
  return (
    <div className="absolute left-3 right-3 top-[60px] z-50 overflow-hidden rounded-xl border border-white/10 bg-black/80 shadow-2xl backdrop-blur-xl">
      <div className="border-b border-white/10 px-4 py-3 text-[11px] uppercase tracking-widest text-white/45">
        {multiWorkspace ? "Switch workspace" : "Workspace"}
      </div>
      {multiWorkspace && allWorkspaces ? (
        <ul className="max-h-60 overflow-y-auto py-1">
          {allWorkspaces.map((w) => {
            const active = w.id === workspace?.id;
            return (
              <li key={w.id}>
                <Link
                  href={hrefSwitchWorkspace(w.id)}
                  onClick={onPick}
                  className={cn(
                    "block px-4 py-2.5 text-sm transition",
                    active
                      ? "bg-white/[0.08] font-semibold text-white"
                      : "text-white/75 hover:bg-white/[0.05] hover:text-white",
                  )}
                >
                  <span className="block truncate">{w.name}</span>
                  <span className="block truncate text-[10px] font-normal uppercase tracking-wider text-white/40">
                    {w.slug}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="px-4 py-3 text-[11px] leading-snug text-white/55">
          This account currently has access to{" "}
          <span className="font-semibold text-white/80">{wsLabel}</span>.
        </div>
      )}
      <Link
        href={withWorkspaceHref("/settings")}
        className="block border-t border-white/10 bg-white/[0.02] px-4 py-2.5 text-center text-[11px] font-semibold text-aqua hover:underline"
      >
        Workspace settings →
      </Link>
    </div>
  );
}

function WorkspaceChip({
  label,
  kicker,
  open,
  onToggle,
  className,
}: {
  label: string;
  kicker: string;
  open: boolean;
  onToggle: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-haspopup="menu"
      aria-expanded={open}
      title="Switch workspace"
      className={cn(
        "group inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] transition",
        open
          ? "border-aqua/50 bg-aqua/10 text-aqua"
          : "border-white/10 bg-white/[0.04] text-white/70 hover:border-white/20 hover:text-white",
        className,
      )}
    >
      <span className="grid h-4 w-4 shrink-0 place-items-center rounded-sm bg-gradient-to-br from-lilac via-aqua to-coral text-[8px] font-bold text-ink">
        {initialsOf(label).slice(0, 1)}
      </span>
      <span className="max-w-[14ch] truncate normal-case tracking-normal">
        {label}
      </span>
      <span className="text-[8px] tracking-wider text-white/40 group-hover:text-white/70">
        {kicker}
      </span>
      <span className="ml-0.5 text-white/40">⌄</span>
    </button>
  );
}

function initialsOf(name: string): string {
  return name
    .replace(/[^a-zA-Z0-9 ]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("") || "?";
}

function initialsFromMe(displayName: string | null, email: string): string {
  const source = (displayName ?? "").trim() || email;
  const parts = source.split(/[\s@.]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (parts[0]?.slice(0, 2) ?? "??").toUpperCase();
}
