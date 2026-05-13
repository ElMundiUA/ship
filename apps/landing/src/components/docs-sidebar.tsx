"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { DOCS_NAV } from "@/lib/docs-nav";

/**
 * Two-column shell for /docs/*.
 *
 *   Desktop (lg+): sticky left rail with grouped section nav + the content slot
 *                  to its right. The content slot itself can opt into a third,
 *                  TOC rail by rendering an <aside class="docs-toc-rail"> as
 *                  its last child (handled by the per-page route).
 *   Mobile:        the left rail collapses into a horizontal scrollable pill bar
 *                  above the content, so users can still hop between sections.
 */
export function DocsSidebar({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const flat = DOCS_NAV.flatMap((g) => g.items);

  /* Match longer routes first so /docs/protocol/rfc-0001 resolves to
   * "/docs/protocol", not "/docs". */
  const sorted = [...flat].sort((a, b) => b.href.length - a.href.length);
  const activeHref = sorted.find(
    (item) => pathname === item.href || pathname.startsWith(item.href + "/"),
  )?.href ?? "/docs";

  return (
    <div className="docs-shell">
      <aside className="docs-sidebar">
        {/* Mobile pill bar */}
        <nav className="docs-mobile-nav" aria-label="Docs sections (mobile)">
          {flat.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={item.href === activeHref ? "page" : undefined}
              className="docs-mobile-nav-link"
            >
              {item.label}
            </Link>
          ))}
        </nav>

        {/* Desktop sticky sidebar */}
        <div className="docs-sidebar-sticky hidden lg:block">
          {DOCS_NAV.map((group) => (
            <div key={group.label} className="mb-6">
              <p className="docs-sidebar-group-label">{group.label}</p>
              <ul className="space-y-0.5">
                {group.items.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={item.href === activeHref ? "page" : undefined}
                      className="docs-sidebar-link"
                    >
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </aside>

      {children}
    </div>
  );
}
