import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Static skeleton that mirrors `AppShell`'s sidebar + top-bar geometry so
 * `loading.tsx` files can render a usable shape the moment a navigation
 * starts, before the route's server component finishes its data fetch.
 *
 * Intentionally not wired to any data: this is a pure layout placeholder
 * and must stay synchronous so Next can stream it instantly. Pages that
 * want a richer body skeleton pass `children`; the default body is a
 * trio of pulsing cards.
 */
export function LoadingShell({
  children,
  rows = 3,
}: {
  children?: ReactNode;
  rows?: number;
}) {
  const navRows = 6;
  return (
    <div className="min-h-screen bg-ink text-mist">
      <div className="flex w-full gap-0">
        <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-white/10 bg-black/30 backdrop-blur-xl lg:flex">
          <div className="border-b border-white/10 px-4 py-5">
            <div className="font-display text-base font-bold tracking-tight text-white">
              Ship<span className="text-aqua">.</span>
              <span className="ml-1 rounded-md border border-aqua/40 bg-aqua/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua/90">
                cloud
              </span>
            </div>
          </div>
          <div className="border-b border-white/10 px-3 py-3">
            <div className="h-10 w-full animate-pulse rounded-xl bg-white/[0.06]" />
          </div>
          <nav className="flex-1 overflow-y-auto px-2 py-3">
            <div className="px-3 pb-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-white/35">
              Workspace
            </div>
            <ul className="space-y-1">
              {Array.from({ length: navRows }).map((_, i) => (
                <li key={i} className="px-3 py-1.5">
                  <div
                    className="h-3 animate-pulse rounded bg-white/[0.06]"
                    style={{ width: `${60 + ((i * 13) % 30)}%` }}
                  />
                </li>
              ))}
            </ul>
          </nav>
          <div className="border-t border-white/10 p-3">
            <div className="h-9 w-full animate-pulse rounded-md bg-white/[0.06]" />
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-40 border-b border-white/10 bg-ink/80 backdrop-blur-xl">
            <div className="flex items-center gap-3 px-6 py-4 lg:px-8">
              <div
                aria-hidden
                className="h-10 w-10 shrink-0 animate-pulse rounded-lg bg-white/[0.06] lg:hidden"
              />
              <div className="min-w-0 flex-1 space-y-2">
                <div className="h-3 w-24 animate-pulse rounded bg-white/[0.06]" />
                <div className="h-6 w-64 animate-pulse rounded bg-white/[0.08]" />
              </div>
            </div>
          </header>
          <main className="px-6 pb-16 pt-6 lg:px-8 lg:pb-20 lg:pt-8">
            {children ?? <SkeletonRows rows={rows} />}
          </main>
        </div>
      </div>
    </div>
  );
}

export function SkeletonRows({
  rows = 3,
  className,
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div className={cn("space-y-4", className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-24 animate-pulse rounded-2xl border border-white/10 bg-white/[0.04]"
        />
      ))}
    </div>
  );
}

/**
 * Skeleton body for routes that render INSIDE the `(authed)/layout.tsx`
 * chrome — the layout already provides the sidebar + top-bar, so
 * `loading.tsx` here must NOT use {@link LoadingShell} (that paints a
 * second sidebar). Renders just a sticky header placeholder and a body.
 */
export function LoadingBody({
  children,
  rows = 3,
}: {
  children?: ReactNode;
  rows?: number;
}) {
  return (
    <>
      <header className="sticky top-0 z-40 border-b border-white/10 bg-ink/80 backdrop-blur-xl">
        <div className="flex items-center gap-3 px-6 py-4 lg:px-8">
          <div
            aria-hidden
            className="h-10 w-10 shrink-0 animate-pulse rounded-lg bg-white/[0.06] lg:hidden"
          />
          <div className="min-w-0 flex-1 space-y-2">
            <div className="h-3 w-24 animate-pulse rounded bg-white/[0.06]" />
            <div className="h-6 w-64 animate-pulse rounded bg-white/[0.08]" />
          </div>
        </div>
      </header>
      <div className="px-6 pb-16 pt-6 lg:px-8 lg:pb-20 lg:pt-8">
        {children ?? <SkeletonRows rows={rows} />}
      </div>
    </>
  );
}
