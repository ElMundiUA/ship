"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo } from "react";
import { cn } from "@/lib/cn";

/**
 * Navigator is the conversational agent that can drive almost every
 * surface in the console — dispatching requests, editing lanes,
 * answering clarifications, drafting new catalog patterns. It's an
 * *action layer*, not a triage queue, so it lives in the header as
 * a globally-reachable launcher (⌘K / Ctrl-K) instead of hiding in
 * the sidebar nav next to passive-reading pages.
 *
 * Pressing the pill or the hotkey navigates to ``/chat`` with the
 * current scope-pill selection (``?scope=&repo_id=&project_id=``)
 * preserved, so the agent opens in the same context the operator
 * was just looking at. Staying scope-aware here matters — a BA
 * request from inside ``acme/api`` should default to that repo.
 *
 * The cmd-palette hotkey intentionally skips when a modifier other
 * than ⌘/Ctrl is held (shift/alt) so browser shortcuts like
 * ``Ctrl+Shift+K`` (devtools console) keep working.
 */

const SCOPE_KEYS = ["scope", "repo_id", "project_id"] as const;

/**
 * Wrapping the launcher in ``Suspense`` keeps Next's static prerender
 * happy — ``useSearchParams`` otherwise bails every page out of SSG.
 * The fallback is a pixel-match link to ``/chat`` (no scope preserved)
 * so the header doesn't shift or flash; the live component swaps in
 * once the client hydrates the search-param state.
 */
export function NavigatorLauncher() {
  return (
    <Suspense fallback={<NavigatorPill href="/chat" active={false} />}>
      <NavigatorLauncherInner />
    </Suspense>
  );
}

function NavigatorLauncherInner() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const href = useMemo(() => {
    const qs = new URLSearchParams();
    for (const key of SCOPE_KEYS) {
      const value = params.get(key);
      if (value) qs.set(key, value);
    }
    const suffix = qs.toString();
    return suffix ? `/chat?${suffix}` : "/chat";
  }, [params]);

  const active = pathname?.startsWith("/chat") ?? false;

  useEffect(() => {
    // Prefetch so the first keystroke feels instant.
    router.prefetch(href);
  }, [router, href]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const mod = event.metaKey || event.ctrlKey;
      if (!mod) return;
      if (event.shiftKey || event.altKey) return;
      if (event.key.toLowerCase() !== "k") return;
      event.preventDefault();
      router.push(href);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [router, href]);

  return <NavigatorPill href={href} active={active} />;
}

function NavigatorPill({ href, active }: { href: string; active: boolean }) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      title="Navigator — conversational control surface (⌘K)"
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold transition",
        active
          ? "border-aqua/60 bg-aqua/10 text-aqua"
          : "border-white/10 bg-white/[0.04] text-white/75 hover:border-aqua/40 hover:text-white",
      )}
      data-testid="navigator-launcher"
    >
      <NavigatorGlyph active={active} />
      <span>Navigator</span>
      <kbd className="hidden items-center gap-0.5 rounded border border-white/15 bg-black/30 px-1.5 py-0.5 font-mono text-[10px] font-bold text-white/55 md:inline-flex">
        <span aria-hidden>⌘</span>
        <span>K</span>
      </kbd>
    </Link>
  );
}

function NavigatorGlyph({ active }: { active: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid h-5 w-5 shrink-0 place-items-center rounded-md text-[11px] font-bold text-ink transition",
        active
          ? "bg-aqua"
          : "bg-gradient-to-br from-aqua via-lilac to-coral",
      )}
    >
      ✦
    </span>
  );
}
