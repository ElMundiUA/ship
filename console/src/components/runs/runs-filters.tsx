"use client";

import { cn } from "@/lib/cn";

import {
  RUN_STATUSES,
  RUN_TRIGGERS,
  type RunStatus,
  type RunTrigger,
  type RunsFilterState,
  buildRunsUrl,
  countActiveRunsFilters,
} from "./runs-url";

/**
 * Filter chip row for the outcome-first ``/runs`` list (RFC-0010
 * Wave 6 / Phase 3 ticket P3-06).
 *
 * Five chip dimensions, mirroring the inbox-filters control pattern:
 *   - Play (single)        — filters by pipeline / play key
 *   - Repo (single)        — filters by activated-repo UUID
 *   - Status (multi)       — running | succeeded | failed | cancelled
 *   - Trigger (multi)      — manual | webhook | cron | onboarding
 *   - Has escalations (bool) — only show runs with >=1 escalation
 *
 * State is owned by the parent (so it can mirror to the URL via
 * ``useSearchParams`` + ``router.push``). This component is the
 * "view" half of the controlled-pattern: it renders the current
 * state and emits ``onChange(next)`` for every interaction.
 *
 * Counts (next to each multi-select chip) are optional — pass
 * ``counts={...}`` to decorate, omit for static demos.
 */

const STATUS_LABEL: Record<RunStatus, string> = {
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

const TRIGGER_LABEL: Record<RunTrigger, string> = {
  manual: "Manual",
  webhook: "Webhook",
  cron: "Scheduled",
  onboarding: "Onboarding",
};

export type RunsFiltersOption = {
  /** URL-safe value (e.g. pipeline_id, repo_id). */
  value: string;
  /** Human label (e.g. "scan-secrets", "helio/ship"). */
  label: string;
  /** Optional secondary text (e.g. lane kind under play name). */
  hint?: string;
  /** Optional run count for this option. */
  count?: number;
};

export type RunsFiltersProps = {
  value: RunsFilterState;
  onChange: (next: RunsFilterState) => void;
  /** Available plays (pipelines) the operator can pick from. */
  playOptions: RunsFiltersOption[];
  /** Available repos (activated) the operator can pick from. */
  repoOptions: RunsFiltersOption[];
  /** Optional per-status / per-trigger counts shown next to each chip. */
  counts?: {
    statuses?: Partial<Record<RunStatus, number>>;
    triggers?: Partial<Record<RunTrigger, number>>;
    /** Count of runs with at least one escalation (drives the bool chip). */
    withEscalations?: number;
  };
  className?: string;
};

function toggleEnum<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}

export function RunsFilters({
  value,
  onChange,
  playOptions,
  repoOptions,
  counts,
  className,
}: RunsFiltersProps) {
  const activeCount = countActiveRunsFilters(value);

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div className="flex flex-wrap items-center gap-2">
        {playOptions.length > 0 && (
          <Dropdown
            label="Play"
            placeholder="Any play"
            value={value.play}
            options={playOptions}
            onChange={(next) => onChange({ ...value, play: next })}
          />
        )}

        {repoOptions.length > 0 && (
          <Dropdown
            label="Repo"
            placeholder="Any repo"
            value={value.repo}
            options={repoOptions}
            onChange={(next) => onChange({ ...value, repo: next })}
          />
        )}

        <ChipGroup
          label={
            value.statuses.length > 0
              ? `Status (${value.statuses.length} selected)`
              : "Status"
          }
        >
          {RUN_STATUSES.map((s) => {
            const active = value.statuses.includes(s);
            const count = counts?.statuses?.[s];
            return (
              <ChipButton
                key={s}
                active={active}
                count={count}
                onClick={() =>
                  onChange({ ...value, statuses: toggleEnum(value.statuses, s) })
                }
              >
                <StatusDot status={s} />
                {STATUS_LABEL[s]}
              </ChipButton>
            );
          })}
        </ChipGroup>

        <ChipGroup
          label={
            value.triggers.length > 0
              ? `Trigger (${value.triggers.length} selected)`
              : "Trigger"
          }
        >
          {RUN_TRIGGERS.map((t) => {
            const active = value.triggers.includes(t);
            const count = counts?.triggers?.[t];
            return (
              <ChipButton
                key={t}
                active={active}
                count={count}
                onClick={() =>
                  onChange({ ...value, triggers: toggleEnum(value.triggers, t) })
                }
              >
                {TRIGGER_LABEL[t]}
              </ChipButton>
            );
          })}
        </ChipGroup>

        <ChipButton
          active={value.hasEscalations}
          count={counts?.withEscalations}
          onClick={() =>
            onChange({ ...value, hasEscalations: !value.hasEscalations })
          }
        >
          ⚠ Has escalations
        </ChipButton>

        {activeCount > 0 && (
          <a
            href={buildRunsUrl({
              play: null,
              repo: null,
              statuses: [],
              triggers: [],
              hasEscalations: false,
            })}
            className="ml-auto text-[11px] font-semibold text-aqua/80 hover:text-aqua"
            onClick={(e) => {
              // Let the parent reset state immediately (avoids the
              // momentary flicker of router.push roundtripping); the
              // ``href`` stays set so the link is still meaningful for
              // middle-click / no-JS users.
              e.preventDefault();
              onChange({
                play: null,
                repo: null,
                statuses: [],
                triggers: [],
                hasEscalations: false,
              });
            }}
          >
            Clear filters →
          </a>
        )}
      </div>
    </div>
  );
}

function ChipGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="mr-1 text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </span>
      {children}
    </div>
  );
}

function ChipButton({
  active,
  count,
  onClick,
  children,
}: {
  active: boolean;
  count?: number;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-semibold transition",
        active
          ? "border-aqua/50 bg-aqua/15 text-aqua"
          : "border-white/10 bg-white/[0.04] text-white/65 hover:border-white/20 hover:text-white/85",
      )}
    >
      {children}
      {count !== undefined && (
        <span
          className={cn(
            "rounded-full px-1.5 text-[10px] font-bold",
            active ? "bg-aqua/30 text-white" : "bg-white/10 text-white/55",
          )}
        >
          {count}
        </span>
      )}
    </button>
  );
}

function Dropdown({
  label,
  placeholder,
  value,
  options,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string | null;
  options: RunsFiltersOption[];
  onChange: (next: string | null) => void;
}) {
  const active = value !== null;
  // Keep the native ``<select>``: zero JS for keyboard a11y, no
  // popover positioning headaches, and the chip styling carries the
  // visual weight. ``onChange`` translates "" → null (the "any" option).
  return (
    <label
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-semibold transition",
        active
          ? "border-aqua/50 bg-aqua/15 text-aqua"
          : "border-white/10 bg-white/[0.04] text-white/65 hover:border-white/20 hover:text-white/85",
      )}
    >
      <span className="text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </span>
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
        className="cursor-pointer bg-transparent text-[11px] font-semibold text-current focus:outline-none"
      >
        <option value="" className="bg-ink text-white">
          {placeholder}
        </option>
        {options.map((opt) => (
          <option key={opt.value} value={opt.value} className="bg-ink text-white">
            {opt.label}
            {opt.hint ? ` · ${opt.hint}` : ""}
            {opt.count !== undefined ? ` (${opt.count})` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

function StatusDot({ status }: { status: RunStatus }) {
  const color =
    status === "running"
      ? "bg-sky-400"
      : status === "succeeded"
        ? "bg-emerald-400"
        : status === "failed"
          ? "bg-coral"
          : "bg-white/40";
  return (
    <span
      aria-hidden
      className={cn("h-1.5 w-1.5 rounded-full", color)}
    />
  );
}
