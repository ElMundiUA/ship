"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  createContext,
  type ReactNode,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { cn } from "@/lib/cn";
import { EnvSeparationWarningModal } from "@/components/env-separation-warning-modal";
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
/**
 * MCP-first rail (ELS-289): exactly two entries — Chat (the fallback
 * Navigator client) and Settings. Inbox operation moved to the
 * operator's agent over MCP / Telegram; the per-item ``/approve/{id}``
 * confirm page is deep-link only and never appears in the rail.
 * Process / Knowledge / Policies stay routable via Settings → General
 * (Advanced surfaces).
 */
function buildWorkspaceNav(): NavGroup[] {
  return [
    {
      section: "Workspace",
      items: [
        { href: "/chat", label: "Chat", icon: <DotIcon /> },
        { href: "/settings", label: "Settings", icon: <DotIcon /> },
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

// ELS-235: nav hrefs visible per console mode. The /approve confirm
// surface stays reachable in EVERY mode but is deep-link only — it
// never renders in the rail.
const RESIDUAL_NAV_HREFS = new Set(["/chat", "/settings"]);
const OFF_NAV_HREFS = new Set<string>([]);

function navFor(
  pathname: string | null,
  opts?: {
    consoleMode?: "full" | "residual" | "off";
  },
): NavGroup[] {
  const slug = parseRepoSlug(pathname);
  const raw = slug ? buildRepoNav(slug) : buildWorkspaceNav();
  const mode = opts?.consoleMode ?? "full";
  const allowed =
    mode === "residual" ? RESIDUAL_NAV_HREFS : mode === "off" ? OFF_NAV_HREFS : null;
  return raw
    .map((g) => ({
      section: g.section,
      items: (SHOW_STUBS ? g.items : g.items.filter((i) => !i.stub)).filter(
        (i) => allowed === null || allowed.has(i.href),
      ),
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

type MobileNavContextValue = {
  open: boolean;
  setOpen: (next: boolean) => void;
  toggle: () => void;
  menuButtonRef: React.RefObject<HTMLButtonElement | null>;
};

const MobileNavContext = createContext<MobileNavContextValue | null>(null);

function useMobileNav(): MobileNavContextValue | null {
  return useContext(MobileNavContext);
}

function MobileNavMenuButton() {
  const nav = useMobileNav();
  if (!nav) return null;
  return (
    <button
      ref={nav.menuButtonRef}
      type="button"
      className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-white/10 bg-white/[0.04] text-white/80 transition hover:bg-white/[0.08] hover:text-white lg:hidden"
      aria-label="Open navigation menu"
      aria-expanded={nav.open}
      aria-controls="mobile-nav-drawer"
      onClick={nav.toggle}
    >
      <svg
        aria-hidden
        viewBox="0 0 24 24"
        className="h-5 w-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      >
        <path d="M4 7h16M4 12h16M4 17h16" />
      </svg>
    </button>
  );
}

type SidebarPanelProps = {
  wsLabel: string;
  wsKicker: string;
  wsOpen: boolean;
  setWsOpen: (next: boolean | ((prev: boolean) => boolean)) => void;
  pathname: string | null;
  workspace?: AppShellWorkspace;
  allWorkspaces?: AppShellWorkspace[];
  multiWorkspace: boolean;
  withWorkspaceHref: (href: string) => string;
  nav: NavGroup[];
  allNavHrefs: string[];
  repoChip: { slug: string } | null;
  userInfo: AppShellUser;
  onNavInteract?: () => void;
};

function SidebarPanel({
  wsLabel,
  wsKicker,
  wsOpen,
  setWsOpen,
  pathname,
  workspace,
  allWorkspaces,
  multiWorkspace,
  withWorkspaceHref,
  nav,
  allNavHrefs,
  repoChip,
  userInfo,
  onNavInteract,
}: SidebarPanelProps) {
  const onPickWorkspace = () => {
    setWsOpen(false);
    onNavInteract?.();
  };
  return (
    <>
      <div className="border-b border-white/10 px-4 py-5">
        <Link
          href={withWorkspaceHref("/")}
          className="font-display text-base font-bold tracking-tight text-white"
          onClick={onNavInteract}
        >
          Ship<span className="text-aqua">.</span>
          <span className="ml-1 rounded-md border border-aqua/40 bg-aqua/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua/90">
            cloud
          </span>
        </Link>
      </div>

      <WorkspaceSwitcherBlock
        wsLabel={wsLabel}
        wsKicker={wsKicker}
        wsOpen={wsOpen}
        setWsOpen={setWsOpen}
        pathname={pathname}
        workspace={workspace}
        allWorkspaces={allWorkspaces}
        multiWorkspace={multiWorkspace}
        withWorkspaceHref={withWorkspaceHref}
        onPick={onPickWorkspace}
      />

      {repoChip && (
        <div className="border-b border-white/10 px-3 py-3">
          <Link
            href={withWorkspaceHref("/")}
            className="mb-2 inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-widest text-white/45 hover:text-white"
            title="Back to workspace home"
            onClick={onNavInteract}
          >
            <span aria-hidden>←</span>
            <span>Workspace</span>
          </Link>
          <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-2">
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-gradient-to-br from-lilac via-aqua to-coral text-[10px] font-bold text-ink">
              {initialsOf(repoChip.slug.split("/", 1)[0] ?? repoChip.slug)}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[10px] font-semibold uppercase tracking-widest text-white/45">
                {repoChip.slug.split("/", 1)[0]}
              </span>
              <span className="block truncate text-sm font-semibold text-white">
                {repoChip.slug.split("/").slice(1).join("/") || repoChip.slug}
              </span>
            </span>
          </div>
        </div>
      )}

      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {nav.map((group) => (
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
                      onClick={onNavInteract}
                      className={cn(
                        "flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm transition",
                        active
                          ? "bg-white/[0.08] text-white shadow-[inset_2px_0_0_theme(colors.aqua)]"
                          : "text-white/65 hover:bg-white/[0.04] hover:text-white",
                      )}
                    >
                      <span className={active ? "text-aqua" : "text-white/50"}>
                        {item.icon}
                      </span>
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

      <div className="border-t border-white/10 p-3">
        <div className="flex items-center gap-2 rounded-md px-2 py-1.5">
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-gradient-to-br from-aqua via-lilac to-coral text-[10px] font-bold text-ink">
            {userInfo.initials}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs font-semibold text-white">
              {userInfo.name}
            </div>
            <div className="truncate text-[10px] text-white/40">
              {userInfo.email}
            </div>
          </div>
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
    </>
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

/**
 * Backwards-compatible single-component shell. New code should prefer
 * rendering {@link AppShellChrome} once at the layout level and a
 * per-page {@link PageHeader} inside the content tree — that lets
 * sibling-route navigation skip re-fetching workspaces and re-mounting
 * the sidebar. This wrapper is kept so older pages that still own their
 * own `<AppShell>` block continue to render unchanged during migration.
 */
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
  const wsKicker = workspace?.slug;
  const dedupedKicker = kicker && kicker !== wsKicker ? kicker : undefined;
  return (
    <AppShellChrome
      workspace={workspace}
      allWorkspaces={allWorkspaces}
      me={me}
    >
      <PageHeader
        title={title}
        kicker={dedupedKicker}
        actions={actions}
        scopePill={scopePill}
      />
      <PageBody>{children}</PageBody>
    </AppShellChrome>
  );
}

/**
 * Sidebar + scrollable main column without the per-page title bar.
 * Rendered once by `app/(authed)/layout.tsx` so navigation between
 * sibling routes does not re-mount or re-fetch chrome data.
 *
 * Pages render their own {@link PageHeader} as the first child; the
 * header sticks to the top of the scroll area via CSS so the visual
 * result matches the legacy `<AppShell>` block.
 */
export function AppShellChrome({
  children,
  workspace,
  allWorkspaces,
  me,
  consoleMode,
}: {
  children: ReactNode;
  workspace?: AppShellWorkspace;
  allWorkspaces?: AppShellWorkspace[];
  me?: AppShellUser | null;
  /** ELS-235: per-workspace console surface (default full). */
  consoleMode?: "full" | "residual" | "off";
}) {
  const pathname = usePathname();
  const [wsOpen, setWsOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const closeMobileNav = useCallback(() => {
    setMobileNavOpen(false);
    menuButtonRef.current?.focus();
  }, []);
  const toggleMobileNav = useCallback(() => {
    setMobileNavOpen((open) => !open);
  }, []);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prevOverflow;
    };
  }, [mobileNavOpen]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      closeMobileNav();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [mobileNavOpen, closeMobileNav]);
  const defaultWs = { name: "Workspace", org: "Organization" };
  // The layout passes ``workspace`` based on the SSR cookie, but
  // middleware can't update the cookie *for the same request* — so
  // right after a ``?ws=`` switch the layout still sees the previous
  // workspace. To keep the sidebar in sync we prefer the URL's
  // ``?ws=`` (resolved against ``allWorkspaces``) over the prop.
  const urlWsId =
    useSearchParams()?.get("ws")?.trim() || null;
  const urlWs =
    urlWsId && allWorkspaces
      ? allWorkspaces.find((w) => w.id === urlWsId) ?? null
      : null;
  const effectiveWorkspace = urlWs ?? workspace;
  const wsLabel = effectiveWorkspace?.name ?? defaultWs.name;
  const wsKicker = effectiveWorkspace?.slug ?? defaultWs.org;
  const multiWorkspace =
    Boolean(effectiveWorkspace?.id) &&
    Array.isArray(allWorkspaces) &&
    allWorkspaces.length > 1;
  const withWorkspaceHref = (href: string) => {
    if (!multiWorkspace || !effectiveWorkspace?.id) {
      return href;
    }
    const u = new URL(href, "http://local.workspace");
    u.searchParams.set("ws", effectiveWorkspace.id);
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
  const NAV = navFor(pathname, { consoleMode });
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

  const sidebarProps: SidebarPanelProps = {
    wsLabel,
    wsKicker,
    wsOpen,
    setWsOpen,
    pathname,
    workspace: effectiveWorkspace,
    allWorkspaces,
    multiWorkspace,
    withWorkspaceHref,
    nav: NAV,
    allNavHrefs,
    repoChip,
    userInfo,
  };

  const mobileNavContext: MobileNavContextValue = {
    open: mobileNavOpen,
    setOpen: setMobileNavOpen,
    toggle: toggleMobileNav,
    menuButtonRef,
  };

  return (
    // No ``bg-ink`` here — the body already paints the brand radials
    // (lilac top-centre, gold top-right, coral mid-left) and an ink fill
    // on the chrome wrapper would hide them. ``text-mist`` keeps the
    // default body colour for the operator content.
    <div className="min-h-screen text-mist">
      <div className="flex w-full gap-0">
        {/* sidebar — semi-translucent so the body radials still bleed
            through, with the same inset blik landing's panels use. */}
        <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-white/10 bg-black/20 shadow-[inset_-1px_0_0_rgba(255,255,255,0.04)] backdrop-blur-xl lg:flex">
          <SidebarPanel {...sidebarProps} />
        </aside>

        {mobileNavOpen ? (
          <div
            className="fixed inset-0 z-50 lg:hidden"
            role="dialog"
            aria-modal="true"
            id="mobile-nav-drawer"
          >
            <button
              type="button"
              className="absolute inset-0 bg-black/60"
              aria-label="Close navigation menu"
              onClick={closeMobileNav}
            />
            <aside className="relative flex h-full w-[min(100%,20rem)] max-w-full flex-col border-r border-white/10 bg-black/20 shadow-[inset_-1px_0_0_rgba(255,255,255,0.04)] backdrop-blur-xl">
              <SidebarPanel {...sidebarProps} onNavInteract={closeMobileNav} />
            </aside>
          </div>
        ) : null}

        {/* main column */}
        <div className="flex min-w-0 flex-1 flex-col">
          <MobileNavContext.Provider value={mobileNavContext}>
            <main className="flex flex-col">{children}</main>
          </MobileNavContext.Provider>
        </div>
      </div>
      {effectiveWorkspace?.id ? (
        <EnvSeparationWarningModal workspaceId={effectiveWorkspace.id} />
      ) : null}
    </div>
  );
}

/**
 * Per-page sticky title bar. Renders as the first child of the layout's
 * scrollable main column so the look matches the legacy
 * `<AppShell title kicker actions scopePill>` block, but the chrome
 * around it is owned by the layout and persists across navigations.
 */
export function PageHeader({
  title,
  kicker,
  actions,
  scopePill,
}: {
  title: string;
  kicker?: string;
  actions?: ReactNode;
  scopePill?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-40 border-b border-white/10 bg-ink/60 shadow-[inset_0_-1px_0_rgba(255,255,255,0.04)] backdrop-blur-xl">
      <div className="flex items-center gap-3 px-6 py-4 lg:px-8">
        <MobileNavMenuButton />
        <div className="relative min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {scopePill}
            {kicker && (
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
  );
}

/**
 * Body padding wrapper used by pages that want the legacy
 * `<AppShell>` content padding (px-6 pb-16 pt-6 lg:px-8 lg:pb-20 lg:pt-8)
 * around their main content. Pure CSS — no client logic.
 */
export function PageBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("px-6 pb-16 pt-6 lg:px-8 lg:pb-20 lg:pt-8", className)}>
      {children}
    </div>
  );
}

/**
 * Sidebar block that hosts the workspace chip + the popover with the
 * switcher list. Owns its own outside-click / Escape handling and
 * isolates ``useSearchParams`` inside the menu for Next prerender.
 */
function WorkspaceSwitcherBlock({
  wsLabel,
  wsKicker,
  wsOpen,
  setWsOpen,
  pathname,
  workspace,
  allWorkspaces,
  multiWorkspace,
  withWorkspaceHref,
  onPick,
}: {
  wsLabel: string;
  wsKicker: string;
  wsOpen: boolean;
  setWsOpen: (next: boolean | ((prev: boolean) => boolean)) => void;
  pathname: string | null;
  workspace?: AppShellWorkspace;
  allWorkspaces?: AppShellWorkspace[];
  multiWorkspace: boolean;
  withWorkspaceHref: (href: string) => string;
  onPick?: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!wsOpen) return;
    const onPointerDown = (event: MouseEvent | TouchEvent) => {
      const node = ref.current;
      if (!node) return;
      const target = event.target as Node | null;
      if (target && node.contains(target)) return;
      setWsOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setWsOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [wsOpen, setWsOpen]);
  return (
    <div ref={ref} className="relative border-b border-white/10 px-3 py-3">
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
            onPick={() => {
              setWsOpen(false);
              onPick?.();
            }}
          />
        </Suspense>
      )}
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
        href={withWorkspaceHref("/settings/workspaces#create-workspace")}
        onClick={onPick}
        className="flex items-center justify-between gap-2 border-t border-white/10 bg-white/[0.02] px-4 py-2.5 text-[11px] font-semibold text-aqua hover:bg-aqua/[0.04]"
      >
        <span>Create new workspace</span>
        <span aria-hidden className="text-base leading-none">+</span>
      </Link>
      {multiWorkspace && (
        <Link
          href={withWorkspaceHref("/settings/workspaces")}
          onClick={onPick}
          className="flex items-center justify-between gap-2 border-t border-white/10 bg-white/[0.02] px-4 py-2.5 text-[11px] font-semibold text-white/70 hover:bg-white/[0.04] hover:text-white"
        >
          <span>Manage workspaces</span>
          <span aria-hidden>→</span>
        </Link>
      )}
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
      <svg
        aria-hidden
        viewBox="0 0 10 6"
        className={cn(
          "ml-auto h-[6px] w-[10px] shrink-0 text-white/40 transition-transform",
          open && "rotate-180 text-aqua/70",
        )}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <polyline points="1,1 5,5 9,1" />
      </svg>
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
