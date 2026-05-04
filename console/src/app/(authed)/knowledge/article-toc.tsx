"use client";

/**
 * Article reader TOC — sticky right-rail outline that highlights the
 * section currently in the viewport via ``IntersectionObserver``.
 *
 * Rendered at ``xl+`` only; below that breakpoint the right rail
 * doesn't exist and the TOC is hidden. Section IDs are produced by the
 * same slugify rule the markdown ``h1``/``h2``/``h3`` components use,
 * so the in-prose anchor and the TOC link agree.
 */

import { useEffect, useState } from "react";

import { cn } from "@/lib/cn";

export type TocEntry = {
  id: string;
  level: 1 | 2 | 3;
  text: string;
};

export function ArticleToc({ entries }: { entries: TocEntry[] }) {
  const [active, setActive] = useState<string | null>(
    entries[0]?.id ?? null,
  );

  useEffect(() => {
    if (entries.length === 0) return;

    const targets = entries
      .map((entry) => document.getElementById(entry.id))
      .filter((node): node is HTMLElement => node !== null);

    if (targets.length === 0) return;

    // Highlight the heading whose top has just crossed into the upper
    // 30% of the viewport — same idiom as Stripe Docs / Linear's
    // changelog sidebar. Without the rootMargin trim, every heading
    // intersects on first paint and the TOC oscillates.
    const observer = new IntersectionObserver(
      (records) => {
        const onScreen = records.filter((r) => r.isIntersecting);
        if (onScreen.length === 0) return;
        // Pick the topmost intersecting heading.
        onScreen.sort(
          (a, b) => a.boundingClientRect.top - b.boundingClientRect.top,
        );
        const id = onScreen[0]?.target.id;
        if (id) setActive(id);
      },
      { rootMargin: "-15% 0px -70% 0px", threshold: 0 },
    );

    for (const node of targets) observer.observe(node);
    return () => observer.disconnect();
  }, [entries]);

  if (entries.length === 0) return null;

  return (
    <nav aria-label="Article outline" className="space-y-3">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        On this page
      </p>
      <ul className="space-y-1.5 text-[12px] leading-snug">
        {entries.map((entry) => {
          const isActive = entry.id === active;
          return (
            <li
              key={entry.id}
              className={cn(entry.level === 3 && "pl-3")}
            >
              <a
                href={`#${entry.id}`}
                className={cn(
                  "block py-0.5 transition",
                  isActive
                    ? "text-aqua"
                    : "text-white/55 hover:text-white",
                )}
              >
                {entry.text}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
