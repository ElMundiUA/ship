"use client";

/**
 * URL-bound shell around the (already-shipped) `InboxFilters`
 * component (RFC-0010 P2-12).
 *
 * `InboxFilters` is a pure controlled view — it emits `onChange(next)`
 * whenever the operator clicks a chip. The list page that renders it
 * is a server component and therefore can't pass an `onChange`
 * callback through; this thin client wrapper closes that gap by
 * mapping each `onChange` into a `router.push(buildInboxUrl(next))`,
 * dropping the cursor (a fresh filter snapshot resets pagination)
 * but preserving repo + play scope so the operator stays drilled in.
 */

import { useRouter } from "next/navigation";

import { type InboxFilterState } from "@/lib/inbox-types";

import { InboxFilters, type InboxFiltersProps } from "./inbox-filters";
import { buildInboxUrl } from "./inbox-url";

export type InboxFiltersControlledProps = {
  value: InboxFilterState;
  counts?: InboxFiltersProps["counts"];
  /** Repo scope to preserve across filter changes (URL `repo` param). */
  repo?: string | null;
  /** Play scope to preserve across filter changes (URL `play` param). */
  play?: string | null;
  className?: string;
};

export function InboxFiltersControlled({
  value,
  counts,
  repo,
  play,
  className,
}: InboxFiltersControlledProps) {
  const router = useRouter();
  return (
    <InboxFilters
      value={value}
      counts={counts}
      className={className}
      onChange={(next) =>
        router.push(buildInboxUrl(next, { repo, play }))
      }
    />
  );
}
