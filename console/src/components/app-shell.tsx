"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useState } from "react";
import { cn } from "@/lib/cn";
import { currentUser, workspaces } from "@/lib/mock/cloud";

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
};

// Pages that aren't backed by real `/v1` endpoints yet — kept around as
// rendered routes for design reference but hidden from the operator nav so
// the pilot tenant doesn't bump into "coming soon" walls. Toggle with
// NEXT_PUBLIC_SHIP_SHOW_STUBS=1 to surface them again during console
// development.
const SHOW_STUBS = process.env.NEXT_PUBLIC_SHIP_SHOW_STUBS === "1";

const ALL_NAV: { section: string; items: (NavItem & { stub?: boolean })[] }[] = [
  {
    section: "Operate",
    items: [
      { href: "/", label: "Dashboard", icon: <DotIcon /> },
      { href: "/daily", label: "Daily & retro", icon: <DotIcon />, badge: "3", stub: true },
      { href: "/workflows", label: "Workflow runs", icon: <DotIcon />, stub: true },
    ],
  },
  {
    section: "Knowledge",
    items: [
      { href: "/catalog", label: "Catalog", icon: <DotIcon /> },
      { href: "/catalog/pull-requests", label: "Pull requests", icon: <DotIcon />, badge: "4", stub: true },
      { href: "/knowledge", label: "Buckets", icon: <DotIcon /> },
    ],
  },
  {
    section: "Observe",
    items: [
      { href: "/effectiveness", label: "Effectiveness", icon: <DotIcon />, stub: true },
      { href: "/telemetry", label: "Telemetry", icon: <DotIcon />, stub: true },
    ],
  },
  {
    section: "Configure",
    items: [
      { href: "/settings", label: "Workspace settings", icon: <DotIcon /> },
      { href: "/members", label: "Members", icon: <DotIcon /> },
      { href: "/integrations", label: "Integrations", icon: <DotIcon /> },
    ],
  },
];

const NAV: { section: string; items: NavItem[] }[] = ALL_NAV
  .map((section) => ({
    section: section.section,
    items: SHOW_STUBS
      ? section.items
      : section.items.filter((item) => !item.stub),
  }))
  .filter((section) => section.items.length > 0);

function DotIcon() {
  return (
    <span
      aria-hidden
      className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-70"
    />
  );
}

function isActive(pathname: string | null, href: string): boolean {
  if (!pathname) return false;
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

export function AppShell({
  children,
  title,
  kicker,
  actions,
}: {
  children: ReactNode;
  title: string;
  kicker?: string;
  actions?: ReactNode;
}) {
  const pathname = usePathname();
  const [wsOpen, setWsOpen] = useState(false);
  const ws = workspaces[0];

  return (
    <div className="min-h-screen bg-ink text-mist">
      <div className="mx-auto flex max-w-[1480px] gap-0">
        {/* sidebar */}
        <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-white/10 bg-black/30 backdrop-blur-xl lg:flex">
          <div className="border-b border-white/10 px-4 py-5">
            <Link href="/" className="font-display text-base font-bold tracking-tight text-white">
              Ship<span className="text-aqua">.</span>
              <span className="ml-1 rounded-md border border-aqua/40 bg-aqua/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua/90">
                cloud
              </span>
            </Link>
          </div>

          <div className="border-b border-white/10 px-3 py-3">
            <button
              type="button"
              onClick={() => setWsOpen((s) => !s)}
              className="group flex w-full items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-2 text-left transition hover:border-white/20 hover:bg-white/[0.08]"
            >
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-gradient-to-br from-lilac via-aqua to-coral text-[10px] font-bold text-ink">
                {initialsOf(ws.org)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[10px] font-semibold uppercase tracking-widest text-white/45">
                  {ws.org}
                </span>
                <span className="block truncate text-sm font-semibold text-white">{ws.name}</span>
              </span>
              <span className="text-white/40 transition group-hover:text-white">⌄</span>
            </button>
            {wsOpen && (
              <div className="mt-2 overflow-hidden rounded-lg border border-white/10 bg-black/40 shadow-lg">
                {workspaces.map((w) => (
                  <button
                    key={w.id}
                    type="button"
                    className={cn(
                      "flex w-full items-center gap-2 border-b border-white/5 px-3 py-2 text-left text-xs transition last:border-b-0",
                      w.id === ws.id ? "bg-white/[0.06] text-white" : "text-white/70 hover:bg-white/[0.04] hover:text-white"
                    )}
                  >
                    <span className="grid h-5 w-5 shrink-0 place-items-center rounded bg-white/10 text-[9px] font-bold text-white/80">
                      {initialsOf(w.org)}
                    </span>
                    <span className="flex-1 truncate">
                      <span className="block truncate font-medium">{w.name}</span>
                      <span className="block truncate text-[10px] text-white/40">{w.org} · {w.plan}</span>
                    </span>
                    {w.id === ws.id && <span className="text-aqua">●</span>}
                  </button>
                ))}
                <div className="border-t border-white/10 bg-white/[0.02] p-2 text-center">
                  <button className="text-[11px] font-semibold text-aqua hover:underline">
                    + new workspace
                  </button>
                </div>
              </div>
            )}
          </div>

          <nav className="flex-1 overflow-y-auto px-2 py-3">
            {NAV.map((group) => (
              <div key={group.section} className="mb-4">
                <div className="px-3 pb-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-white/35">
                  {group.section}
                </div>
                <ul className="space-y-0.5">
                  {group.items.map((item) => {
                    const active = isActive(pathname, item.href);
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
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

          <div className="border-t border-white/10 p-3">
            <div className="flex items-center gap-2 rounded-md px-2 py-1.5">
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-gradient-to-br from-aqua via-lilac to-coral text-[10px] font-bold text-ink">
                {currentUser.avatarInitials}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-semibold text-white">{currentUser.name}</div>
                <div className="truncate text-[10px] text-white/40">{currentUser.email}</div>
              </div>
              <Link
                href="/settings"
                className="rounded-md p-1 text-white/40 hover:bg-white/5 hover:text-white"
                aria-label="Account settings"
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
              <div className="min-w-0 flex-1">
                {kicker && (
                  <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-aqua/80">
                    {kicker}
                  </div>
                )}
                <h1 className="font-display truncate text-xl font-bold leading-tight text-white sm:text-2xl">
                  {title}
                </h1>
              </div>
              <div className="hidden items-center gap-2 md:flex">{actions}</div>
            </div>
          </header>
          <main className="px-6 pb-16 pt-6 lg:px-8 lg:pb-20 lg:pt-8">{children}</main>
        </div>
      </div>
    </div>
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
