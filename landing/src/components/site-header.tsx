"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { repoUrl } from "@/lib/config";

/**
 * Top nav, left → right after the logo:
 *   Use cases  — buyer-facing proof / reference deployments
 *   Get started — operator section ( /docs ); also where the wizard lives
 *   Kit         — the consolidated artifact-catalog hub
 *   CLI         — single-binary reference
 *   The book    — long-form rationale (loud accent CTA)
 *   GitHub      — repo
 *
 * The "Get started" slot intentionally points at /docs (not /getting-started).
 * The standalone wizard URL still works and is reachable from the docs sidebar
 * + the docs landing CTA + the home hero. The header surfaces the section
 * because that is what most users actually want when they click "Get started"
 * — orientation first, wizard one click in.
 */
type NavItem = {
  href: string;
  label: string;
  className: string;
  /**
   * Extra path prefixes that should also light up this nav item. Lets us
   * highlight "Get started" while the user is on /getting-started, and
   * highlight "Kit" while they are inside any of the four catalog routes.
   */
  alsoActiveOn?: string[];
};

const NAV: NavItem[] = [
  { href: "/use-cases", label: "Use cases", className: "" },
  {
    href: "/docs",
    label: "Get started",
    className: "",
    alsoActiveOn: ["/getting-started"],
  },
  {
    href: "/kit",
    label: "Kit",
    className: "",
    alsoActiveOn: ["/patterns", "/workflows", "/collections", "/tools"],
  },
  { href: "/cli", label: "CLI", className: "" },
];

/**
 * Pick the active nav item by longest matching prefix across both `href`
 * and any `alsoActiveOn` aliases. Returns the canonical href to compare
 * against in the render loop.
 */
function activeHrefFor(pathname: string | null): string | null {
  if (!pathname) return null;
  // Store state behind a ref-like holder so TypeScript can't narrow
  // the mutation through a closure. Without this wrapper, the
  // `let best: T | null = null` pattern gets narrowed to `never`
  // after the `consider` closure, which blocks the final
  // `best?.href` access (known TS narrowing limitation).
  const state: { best: { href: string; len: number } | null } = {
    best: null,
  };
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
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-3 px-4 sm:px-6">
        <Link href="/" className="shrink-0 font-display text-lg font-bold tracking-normal text-white">
          Ship<span className="text-aqua">.</span>
        </Link>
        <nav className="flex min-w-0 flex-1 items-center justify-end gap-0.5 text-sm sm:gap-1 md:gap-2">
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
                  item.className,
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                {item.label}
              </Link>
            );
          })}
          <Link
            href="/book"
            className="ml-1 inline-flex shrink-0 items-center rounded-full bg-gradient-to-r from-amber-400 via-orange-500 to-fuchsia-600 px-3 py-2 text-xs font-bold uppercase tracking-wide text-zinc-950 shadow-[0_0_24px_rgba(249,115,22,0.45)] ring-2 ring-white/25 transition hover:brightness-110 hover:ring-white/40 sm:ml-2 sm:px-3.5 sm:text-[0.8rem]"
          >
            The book
          </Link>
          <a
            href={repoUrl}
            className="btn-secondary ml-1 shrink-0 !py-2 !text-xs sm:ml-2 sm:!text-sm"
            target="_blank"
            rel="noreferrer"
          >
            GitHub
          </a>
        </nav>
      </div>
    </header>
  );
}
