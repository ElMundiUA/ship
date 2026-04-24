"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";

/**
 * Client-side wrapper for the Play detail drawer (RFC-0010 / Wave 7
 * Phase 4 ticket P4-02).
 *
 * Mounts only when ``?play=<id>`` is present in the URL — the page
 * gates that decision server-side. Owns three concerns:
 *
 *   1. Slide-in animation (``translate-x`` toggled on the first
 *      tick after mount so the drawer transitions from off-screen
 *      → in-view rather than appearing instantly).
 *   2. Close affordances — explicit close button, ``Escape`` key,
 *      backdrop click. Each navigates to ``closeHref`` (which the
 *      server component pre-computes to preserve every other
 *      filter).
 *   3. Focus management — moves keyboard focus into the drawer on
 *      open and traps the close button so ``Escape`` from any
 *      element inside still bubbles correctly.
 *
 * The drawer body is rendered server-side and passed in as
 * ``children``. That keeps data fetching on the server while the
 * shell stays small + interactive.
 */

export function PlayDetailDrawerShell({
  children,
  closeHref,
}: {
  children: ReactNode;
  closeHref: string;
}) {
  const router = useRouter();
  const panelRef = useRef<HTMLDivElement>(null);
  const enteredRef = useRef(false);

  useEffect(() => {
    // Trigger the slide-in by flipping the data-state on the next
    // animation frame. Using a ref so React's Strict Mode double-
    // mount doesn't reset the in-view state mid-transition.
    enteredRef.current = true;
    const id = requestAnimationFrame(() => {
      panelRef.current?.setAttribute("data-state", "open");
    });
    return () => cancelAnimationFrame(id);
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        router.push(closeHref, { scroll: false });
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [router, closeHref]);

  useEffect(() => {
    // Auto-focus the panel so screen-reader users land in the
    // drawer rather than wherever focus happened to be on the grid.
    panelRef.current?.focus();
  }, []);

  return (
    <div
      className="fixed inset-0 z-40"
      role="dialog"
      aria-modal="true"
      aria-label="Play details"
    >
      <button
        type="button"
        aria-label="Close drawer"
        onClick={() => router.push(closeHref, { scroll: false })}
        className="absolute inset-0 cursor-default bg-ink/55 backdrop-blur-[2px] transition-opacity duration-200 ease-out"
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        data-state="closed"
        className={
          "absolute right-0 top-0 h-full w-full max-w-[480px] " +
          "translate-x-full bg-ink/95 shadow-2xl backdrop-blur-xl " +
          "border-l border-white/10 outline-none " +
          "transition-transform duration-200 ease-out " +
          "data-[state=open]:translate-x-0"
        }
      >
        <div className="absolute right-3 top-3 z-10">
          <Link
            href={closeHref}
            scroll={false}
            aria-label="Close"
            className="grid h-8 w-8 place-items-center rounded-full border border-white/10 bg-white/[0.04] text-white/70 hover:border-white/30 hover:bg-white/[0.08] hover:text-white"
          >
            <span aria-hidden className="text-lg leading-none">
              ×
            </span>
          </Link>
        </div>
        <div className="h-full overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}
