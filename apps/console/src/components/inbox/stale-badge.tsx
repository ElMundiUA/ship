import { cn } from "@/lib/cn";

/**
 * Stale-age badge for Inbox item rows (RFC-0010 ticket P2-14).
 *
 * Visualises how long an item has been waiting on the assigned
 * owner. Three bands per the planning doc §6:
 *
 *   < 2 days  → no badge (fresh)
 *   2-6 days  → yellow `Warn` (sun) — "5d waiting"
 *   ≥ 7 days  → red    `Err`  (coral) — "12d waiting"
 *
 * The age is computed against `referenceDate` (defaults to "now") so
 * tests and SSR can pin the clock and avoid timezone drift.
 *
 * For terminal-state items (`resolved` / `dismissed`) the badge
 * always renders nothing — there's nothing to escalate. Snoozed
 * items pause the clock at `snoozedUntil` so a long snooze doesn't
 * inflate apparent staleness.
 *
 * The component is purely presentational: it does not query data,
 * does not subscribe to anything, and does not own any layout
 * around itself. Drop it inside an item row.
 */

export type StaleBadgeStatus = "new" | "snoozed" | "resolved" | "dismissed";

export type StaleBadgeProps = {
  /** When the inbox item was first created (UTC ISO string or Date). */
  createdAt: string | Date;
  /** Item lifecycle state — terminal states render nothing. */
  status: StaleBadgeStatus;
  /** Snooze deadline (ISO string or Date); pauses the clock until then. */
  snoozedUntil?: string | Date | null;
  /** Override "now" for tests / SSR snapshots. */
  referenceDate?: Date;
  className?: string;
};

const MS_PER_DAY = 1000 * 60 * 60 * 24;
const WARN_AT_DAYS = 2;
const ERR_AT_DAYS = 7;

type Severity = "fresh" | "warn" | "err";

function toDate(input: string | Date): Date {
  return input instanceof Date ? input : new Date(input);
}

/**
 * Pure helper exported so the inbox list can sort / filter by
 * staleness without re-implementing the threshold logic. Returns
 * null for terminal-state items (no badge applies).
 */
export function computeStaleness(
  createdAt: string | Date,
  status: StaleBadgeStatus,
  opts: {
    snoozedUntil?: string | Date | null;
    referenceDate?: Date;
  } = {},
): { ageDays: number; severity: Severity } | null {
  if (status === "resolved" || status === "dismissed") return null;

  const now = (opts.referenceDate ?? new Date()).getTime();
  const created = toDate(createdAt).getTime();

  if (status === "snoozed" && opts.snoozedUntil) {
    const snoozedTo = toDate(opts.snoozedUntil).getTime();
    if (snoozedTo > now) {
      // Still snoozed — clock is paused at `now`. Effective age is
      // limited to the time before snooze kicked in. We can't know
      // exactly when the snooze started without another field, so
      // the conservative choice is "treat as fresh while snoozed";
      // the badge re-emerges when `snoozedUntil` passes.
      return { ageDays: 0, severity: "fresh" };
    }
  }

  const ageDays = Math.max(0, (now - created) / MS_PER_DAY);
  let severity: Severity = "fresh";
  if (ageDays >= ERR_AT_DAYS) severity = "err";
  else if (ageDays >= WARN_AT_DAYS) severity = "warn";

  return { ageDays, severity };
}

function formatAge(days: number): string {
  if (days < 1) {
    const hours = Math.max(1, Math.floor(days * 24));
    return `${hours}h waiting`;
  }
  return `${Math.floor(days)}d waiting`;
}

const TONE_CLS: Record<Severity, string> = {
  fresh: "",
  warn: "border-sun/40 bg-sun/15 text-sun",
  err: "border-coral/40 bg-coral/15 text-coral",
};

export function StaleBadge({
  createdAt,
  status,
  snoozedUntil,
  referenceDate,
  className,
}: StaleBadgeProps) {
  const result = computeStaleness(createdAt, status, {
    snoozedUntil,
    referenceDate,
  });
  if (!result || result.severity === "fresh") return null;

  return (
    <span
      title={`Created ${toDate(createdAt).toISOString()}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
        TONE_CLS[result.severity],
        className,
      )}
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
      {formatAge(result.ageDays)}
    </span>
  );
}
