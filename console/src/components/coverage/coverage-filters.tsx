"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";

import { cn } from "@/lib/cn";

/**
 * Filter chip row for the Coverage tab.
 *
 * Renders four controls — a Category dropdown, a "Critical only"
 * toggle chip, a "Has gaps" toggle chip, and a "Clear" link. All four
 * encode their state in the URL (so the user can deep-link a filtered
 * view) and the page re-fetches via the BE endpoint's query params.
 *
 * This is a small client island purely to drive ``router.push`` for
 * the dropdown ``onChange`` and the toggle chips. The buttons are
 * still link-shaped — they could degrade to plain ``<a>`` if JS is
 * off, but doing it in JS keeps the back-stack clean (push instead
 * of nav).
 */

export type CoverageFiltersState = {
  category: string | null;
  criticalOnly: boolean;
  hasGaps: boolean;
};

const PLAY_CATEGORIES: { id: string; label: string }[] = [
  { id: "code_review", label: "Code review" },
  { id: "health_checks", label: "Health checks" },
  { id: "release_ops", label: "Release ops" },
  { id: "incident_response", label: "Incident response" },
  { id: "knowledge_docs", label: "Knowledge & Docs" },
  { id: "planning_process", label: "Planning & Process" },
  { id: "reviewers", label: "Reviewers" },
];

export function CoverageFilters({
  basePath,
  state,
}: {
  basePath: string;
  state: CoverageFiltersState;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  function buildUrl(next: Partial<CoverageFiltersState>): string {
    const merged: CoverageFiltersState = { ...state, ...next };
    const params = new URLSearchParams();
    params.set("tab", "coverage");
    if (merged.category) params.set("category", merged.category);
    if (merged.criticalOnly) params.set("critical_only", "true");
    if (merged.hasGaps) params.set("has_gaps", "true");
    return `${basePath}?${params.toString()}`;
  }

  function navigate(next: Partial<CoverageFiltersState>) {
    startTransition(() => router.push(buildUrl(next)));
  }

  const anyActive =
    state.category !== null || state.criticalOnly || state.hasGaps;

  return (
    <div
      className={cn(
        "mb-4 flex flex-wrap items-center gap-2",
        isPending && "opacity-60",
      )}
    >
      <label className="relative">
        <span className="sr-only">Filter by category</span>
        <select
          value={state.category ?? ""}
          onChange={(e) => navigate({ category: e.target.value || null })}
          className={cn(
            "appearance-none rounded-full border bg-white/[0.04] px-3 py-1.5 pr-8 text-xs font-semibold text-white/85 transition hover:border-white/30",
            state.category
              ? "border-aqua/40 text-aqua"
              : "border-white/15",
          )}
        >
          <option value="">Category: All</option>
          {PLAY_CATEGORIES.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
        <span
          aria-hidden
          className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-white/55"
        >
          ▾
        </span>
      </label>

      <ToggleChip
        label="Critical only"
        active={state.criticalOnly}
        onClick={() => navigate({ criticalOnly: !state.criticalOnly })}
        accent="coral"
      />
      <ToggleChip
        label="Has gaps"
        active={state.hasGaps}
        onClick={() => navigate({ hasGaps: !state.hasGaps })}
        accent="aqua"
      />

      {anyActive && (
        <button
          type="button"
          onClick={() =>
            startTransition(() =>
              router.push(`${basePath}?tab=coverage`),
            )
          }
          className="ml-1 text-xs font-semibold text-white/55 underline-offset-2 hover:text-white hover:underline"
        >
          Clear
        </button>
      )}
    </div>
  );
}

function ToggleChip({
  label,
  active,
  onClick,
  accent,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  accent: "coral" | "aqua";
}) {
  const accentCls =
    accent === "coral"
      ? "border-coral/40 bg-coral/10 text-coral"
      : "border-aqua/40 bg-aqua/10 text-aqua";
  const dotCls = accent === "coral" ? "bg-coral" : "bg-aqua";

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition",
        active
          ? accentCls
          : "border-white/15 bg-white/[0.04] text-white/75 hover:border-white/30",
      )}
    >
      {active && (
        <span className={cn("h-1.5 w-1.5 rounded-full", dotCls)} />
      )}
      {label}
    </button>
  );
}
