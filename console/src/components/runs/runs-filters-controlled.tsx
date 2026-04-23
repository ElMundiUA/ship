"use client";

/**
 * URL-bound shell around the (pure controlled) ``RunsFilters`` view
 * (RFC-0010 / Wave 6 Phase 3 ticket P3-06).
 *
 * ``RunsFilters`` is a pure view — it emits ``onChange(next)`` whenever
 * the operator clicks a chip. The list page that renders it is a
 * server component and therefore can't pass an ``onChange`` callback
 * through; this thin client wrapper closes that gap by mapping each
 * ``onChange`` into a ``router.push(buildRunsUrl(next))``. There's no
 * pagination / cursor on the runs list (server-side window is fixed),
 * so a fresh filter snapshot doesn't need to preserve any extra
 * scope across changes — every interaction produces a clean URL.
 */

import { useRouter } from "next/navigation";

import { RunsFilters, type RunsFiltersProps } from "./runs-filters";
import { type RunsFilterState, buildRunsUrl } from "./runs-url";

export type RunsFiltersControlledProps = {
  value: RunsFilterState;
  playOptions: RunsFiltersProps["playOptions"];
  repoOptions: RunsFiltersProps["repoOptions"];
  counts?: RunsFiltersProps["counts"];
  className?: string;
};

export function RunsFiltersControlled({
  value,
  playOptions,
  repoOptions,
  counts,
  className,
}: RunsFiltersControlledProps) {
  const router = useRouter();
  return (
    <RunsFilters
      value={value}
      playOptions={playOptions}
      repoOptions={repoOptions}
      counts={counts}
      className={className}
      onChange={(next) => router.push(buildRunsUrl(next))}
    />
  );
}
