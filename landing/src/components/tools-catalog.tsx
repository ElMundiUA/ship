"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { ToolEntry, ToolGroup } from "@/lib/tools";
import { cn } from "@/lib/cn";

const TABS: { id: "all" | ToolGroup; label: string }[] = [
  { id: "all", label: "All" },
  { id: "platform", label: "Platform & API" },
  { id: "tracker", label: "Tracker" },
  { id: "ci", label: "CI / scheduler" },
  { id: "e2e", label: "E2E" },
  { id: "agents", label: "Agents" },
];

const GROUP_LABEL: Record<string, string> = {
  platform: "Platform & API",
  tracker: "Tracker",
  ci: "CI & scheduler",
  e2e: "E2E",
  agents: "Agents",
};

export function ToolsCatalog({ tools }: { tools: ToolEntry[] }) {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("all");

  const filtered = useMemo(() => {
    if (tab === "all") return tools;
    return tools.filter((t) => t.group === tab);
  }, [tools, tab]);

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6">
      <div className="mb-8 flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cn(
              "rounded-full border px-4 py-2 text-xs font-bold uppercase tracking-wider transition sm:text-sm",
              tab === t.id
                ? "border-sun/50 bg-sun/15 text-sun"
                : "border-white/15 bg-white/[0.04] text-white/55 hover:border-white/25 hover:text-white/80",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((t) => (
          <li key={t.id}>
            <Link
              href={`/tools/${t.id}`}
              className="group flex h-full flex-col rounded-2xl border border-white/12 bg-gradient-to-br from-white/[0.07] via-white/[0.02] to-transparent p-5 shadow-card transition hover:border-sun/35 hover:shadow-glow"
            >
              <span className="text-[10px] font-bold uppercase tracking-widest text-white/40">
                {GROUP_LABEL[t.group] ?? t.group}
              </span>
              <h3 className="font-display mt-2 text-lg font-bold text-white group-hover:text-sun">{t.title}</h3>
              <p className="mt-2 flex-1 text-sm leading-relaxed text-white/65">{t.summary}</p>
              <div className="mt-4 flex flex-wrap gap-1.5">
                {t.tags.slice(0, 4).map((tag) => (
                  <span
                    key={tag}
                    className="rounded-md border border-white/10 bg-black/30 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-white/45"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
