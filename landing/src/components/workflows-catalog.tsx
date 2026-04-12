"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { WorkflowEntry, WorkflowGroup } from "@/lib/workflows";
import { cn } from "@/lib/cn";

const TABS: { id: "all" | WorkflowGroup; label: string }[] = [
  { id: "all", label: "All" },
  { id: "delivery", label: "Delivery" },
  { id: "quality", label: "Quality" },
  { id: "operations", label: "Operations" },
  { id: "governance", label: "Governance" },
];

const GROUP_LABEL: Record<string, string> = {
  delivery: "Delivery",
  quality: "Quality",
  operations: "Operations",
  governance: "Governance",
};

export function WorkflowsCatalog({ workflows }: { workflows: WorkflowEntry[] }) {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("all");
  const filtered = useMemo(() => {
    if (tab === "all") return workflows;
    return workflows.filter((w) => w.group === tab);
  }, [workflows, tab]);

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
                ? "border-coral/50 bg-coral/15 text-coral"
                : "border-white/15 bg-white/[0.04] text-white/55 hover:border-white/25 hover:text-white/80",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((w) => (
          <li key={w.id}>
            <Link
              href={`/workflows/${w.id}`}
              className="group flex h-full flex-col rounded-2xl border border-white/12 bg-gradient-to-br from-white/[0.07] via-white/[0.02] to-transparent p-5 shadow-card transition hover:border-coral/35 hover:shadow-glow"
            >
              <span className="text-[10px] font-bold uppercase tracking-widest text-white/40">
                {GROUP_LABEL[w.group] ?? w.group}
              </span>
              <h3 className="font-display mt-2 text-lg font-bold text-white group-hover:text-coral">{w.title}</h3>
              <p className="mt-2 flex-1 text-sm leading-relaxed text-white/65">{w.summary}</p>
              <div className="mt-4 flex flex-wrap gap-1.5">
                {w.tags.slice(0, 4).map((tag) => (
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
