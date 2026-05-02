"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { repoUrl } from "@/lib/config";

/**
 * Top nav layout (desktop): three columns with the nav items locked to the
 * horizontal centre.
 *
 *   ┌────────────────────────────────────────────────────────────────┐
 *   │ [ logo ]                  [ NAV ]            [ GH ] [ pill ]   │
 *   └────────────────────────────────────────────────────────────────┘
 *
 * Logo on the left and the right cluster (GitHub icon + combined
 * Closed-beta / Sign-in pill) both sit inside ``flex-1`` flex containers
 * so they balance the centre nav. Below ``md`` the nav hides and the
 * left/right anchors stay; we treat that as acceptable mobile UX until
 * a hamburger gets added.
 *
 * Right cluster:
 *   - GitHub repo link, icon-only ghost button.
 *   - One unified pill: muted "Request access" half (→ /beta) joined to
 *     a solid "Sign in" half (→ console). Reads as a single component
 *     with two purposes — apply if you don't have a workspace yet, sign
 *     in if you do.
 */
type NavItem = {
  href: string;
  label: string;
  alsoActiveOn?: string[];
};

const NAV: NavItem[] = [
  { href: "/use-cases", label: "Use cases" },
  { href: "/process", label: "Process" },
  { href: "/docs", label: "Docs" },
  { href: "/blog", label: "Blog" },
  { href: "/roadmap", label: "Roadmap" },
];

/**
 * Pick the active nav item by longest matching prefix across both `href`
 * and any `alsoActiveOn` aliases.
 */
function activeHrefFor(pathname: string | null): string | null {
  if (!pathname) return null;
  // Boxed mutation so TS doesn't narrow the closure assignment to ``never``.
  const state: { best: { href: string; len: number } | null } = { best: null };
  const consider = (matchPath: string, canonicalHref: string) => {
    if (pathname === matchPath || pathname.startsWith(matchPath + "/")) {
      if (!state.best || matchPath.length > state.best.len) {
        state.best = { href: canonicalHref, len: matchPath.length };
      }
    }
  };
  for (const item of NAV) {
    consider(item.href, item.href);
    for (const alias of item.alsoActiveOn ?? []) {
      consider(alias, item.href);
    }
  }
  return state.best ? state.best.href : null;
}

export function SiteHeader() {
  const pathname = usePathname();
  const active = activeHrefFor(pathname);
  return (
    <header className="fixed inset-x-0 top-0 z-50 border-b border-white/10 bg-ink/70 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[88rem] items-center px-4 sm:px-6">
        {/* Left — logo, takes leftover space so the centre stays centred */}
        <div className="flex flex-1 justify-start">
          <Link
            href="/"
            className="shrink-0 font-display text-lg font-bold tracking-normal text-white"
          >
            Ship<span className="text-aqua">.</span>
          </Link>
        </div>

        {/* Centre — main nav (hidden on small viewports) */}
        <nav className="hidden items-center gap-1 text-sm md:flex md:gap-2">
          {NAV.map((item) => {
            const isActive = item.href === active;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive ? "page" : undefined}
                className={[
                  "shrink-0 rounded-full px-2.5 py-1.5 transition sm:px-3",
                  isActive
                    ? "bg-white/[0.12] font-semibold text-white shadow-[inset_0_0_0_1px_rgba(255,255,255,0.08)]"
                    : "text-white/75 hover:bg-white/[0.06] hover:text-white",
                ].join(" ")}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* Right — book + GitHub icons + combined Request access / Sign in pill */}
        <div className="flex flex-1 items-center justify-end gap-2 sm:gap-3">
          <Link
            href="/book"
            aria-label="The book"
            className="inline-flex h-9 w-9 items-center justify-center rounded-full text-white/60 transition hover:bg-white/[0.06] hover:text-white"
          >
            <BookIcon className="h-[18px] w-[18px]" />
          </Link>
          <a
            href={repoUrl}
            target="_blank"
            rel="noreferrer"
            aria-label="Ship on GitHub"
            className="inline-flex h-9 w-9 items-center justify-center rounded-full text-white/60 transition hover:bg-white/[0.06] hover:text-white"
          >
            <GitHubIcon className="h-[18px] w-[18px]" />
          </a>
          <div className="flex items-center rounded-full bg-white/[0.06] p-0.5">
            <Link
              href="/beta"
              className="inline-flex shrink-0 items-center rounded-full px-3 py-1 text-xs font-medium text-white/70 transition hover:text-white"
            >
              Request access
            </Link>
            <a
              href="https://app.ship.elmundi.com/login"
              className="inline-flex shrink-0 items-center rounded-full bg-gradient-to-b from-[#dcb87a] to-[#b8945a] px-3.5 py-1.5 text-xs font-bold uppercase tracking-wide text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.30)] ring-1 ring-[#cfa96b]/35 transition hover:from-[#e5c489] hover:to-[#c5a06a]"
            >
              Sign in
            </a>
          </div>
        </div>
      </div>
    </header>
  );
}

function GitHubIcon({ className }: { className?: string }) {
  return (
    <svg
      role="img"
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
    </svg>
  );
}

function BookIcon({ className }: { className?: string }) {
  return (
    <svg
      role="img"
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* Spine + bottom edge of an open book */}
      <path d="M4 4.8A1.8 1.8 0 0 1 5.8 3H10a3 3 0 0 1 2 5.2v11.4a3 3 0 0 0-2-5.2H5.8A1.8 1.8 0 0 1 4 12.6V4.8z" />
      <path d="M20 4.8A1.8 1.8 0 0 0 18.2 3H14a3 3 0 0 0-2 5.2v11.4a3 3 0 0 1 2-5.2h4.2A1.8 1.8 0 0 0 20 12.6V4.8z" />
    </svg>
  );
}
