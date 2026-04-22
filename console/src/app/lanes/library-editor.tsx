"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";
import type {
  ApiLane,
  ApiLaneCatalogEntry,
  ApiRepoConfig,
} from "@/lib/api/client";

/**
 * Library-tab editor (Phase 2).
 *
 * Renders one row per lane recipe from ``/v1/catalog/lanes`` and lets
 * the operator toggle enabled / edit the cron for scheduled recipes.
 * Hitting **Save** POSTs to the Next.js action ``/api/lanes/propose``
 * which in turn calls ``POST /v1/.../repos/{id}/config/propose`` — we
 * get back a PR URL and navigate to GitHub so the reviewer can land
 * the change.
 *
 * Design invariants:
 *
 * - The editor never writes back to the Lane projection directly.
 *   The only source of truth the backend trusts is
 *   ``.ship/config.yml`` on the default branch; push-webhook
 *   re-triggers ``sync_lanes_for_repo`` which rebuilds the Lane rows.
 * - Drift handling: the editor carries ``base_sha`` from the GET;
 *   if the backend rejects the proposal with ``409 sha_mismatch``
 *   we surface a banner + disable Save so the operator reloads.
 * - No custom trigger authoring here — that's the Phase 3 ``new``
 *   tab. Library edits are limited to "enable/disable this recipe"
 *   and "change the cron" because those are the two knobs that
 *   match what a recipe already exposes.
 */

type LaneDraft = {
  enabled: boolean;
  schedule: string | null;
  // `origin` lets us render the "can't edit, this was custom-written"
  // hint for lanes that already live in config.yml but don't match
  // any known recipe kind.
  origin: "recipe" | "config-only";
  // Raw trigger data read from ``config.parsed.lanes[<id>]``. We keep
  // it so we can preserve unknown fields (``once``/``event``/
  // ``pattern``/``idempotency_key``) verbatim on save — the editor
  // only touches ``enabled`` + ``schedule``, but the write endpoint
  // wants the full trigger back.
  rawConfig: Record<string, unknown> | null;
};

const CRON_PRESETS: { label: string; value: string; hint: string }[] = [
  {
    label: "Weekday mornings",
    value: "0 9 * * 1-5",
    hint: "Mon–Fri at 09:00 UTC.",
  },
  {
    label: "Every weekday at 06:00",
    value: "0 6 * * 1-5",
    hint: "Mon–Fri at 06:00 UTC — matches daily_standup default.",
  },
  {
    label: "Every Monday at 06:00",
    value: "0 6 * * 1",
    hint: "Weekly tech-debt review window.",
  },
  { label: "Nightly at 04:00", value: "0 4 * * *", hint: "Self-heal cadence." },
  { label: "Daily at 09:00", value: "0 9 * * *", hint: "Every day, once." },
];

export function LibraryEditor({
  workspaceId,
  repoId,
  repoFullName,
  catalog,
  lanes,
  config,
}: {
  workspaceId: string;
  repoId: string;
  repoFullName: string;
  catalog: ApiLaneCatalogEntry[];
  // ``lanes`` here is the already-synced DB projection of the same
  // repo's config.yml (for the "active"/synced hint). The editor
  // writes are always keyed on ``config.parsed.lanes`` — the raw,
  // byte-of-truth source — not on ``lanes``.
  lanes: ApiLane[];
  config: ApiRepoConfig | null;
}) {
  // ------ baseline ---------------------------------------------------
  const baseline = useMemo(
    () => buildBaseline(catalog, config, lanes),
    [catalog, config, lanes],
  );

  const [draft, setDraft] = useState<Record<string, LaneDraft>>(baseline);
  const [summary, setSummary] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [drift, setDrift] = useState<string | null>(null);

  const baseSha = config?.sha ?? null;

  // ------ diff -------------------------------------------------------
  const changes = useMemo(() => diffDrafts(baseline, draft), [baseline, draft]);
  const hasChanges = changes.length > 0;
  const cronInvalid = Object.entries(draft).some(
    ([, d]) => d.enabled && d.schedule !== null && !isValidCron(d.schedule),
  );

  const disableSave = !hasChanges || cronInvalid || submitting || drift !== null;

  // ------ save handler ----------------------------------------------
  async function handleSave() {
    setSubmitting(true);
    setError(null);
    try {
      const lanesBody = buildProposalBody({ catalog, draft });
      const response = await fetch("/api/lanes/propose", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspaceId,
          repoId,
          base_sha: baseSha,
          lanes: lanesBody,
          change_summary: summary.trim() || undefined,
        }),
      });
      const body = (await response.json().catch(() => null)) as
        | {
            pr_url?: string;
            pr_number?: number;
            error?: string;
            code?: string;
            detail?: unknown;
          }
        | null;
      if (!response.ok || !body?.pr_url) {
        if (body?.code === "sha_mismatch") {
          setDrift(
            "HEAD of .ship/config.yml moved since you loaded the editor. Reload the page to re-baseline your edits.",
          );
          return;
        }
        setError(body?.error ?? `Couldn't open the PR (HTTP ${response.status}).`);
        return;
      }
      window.location.href = body.pr_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  }

  // ------ render ----------------------------------------------------
  const rows = useMemo(() => buildRows(catalog, baseline, draft), [
    catalog,
    baseline,
    draft,
  ]);

  return (
    <div className="space-y-4">
      {config && config.exists === false ? (
        <Card className="border-sun/20 bg-sun/5">
          <p className="text-xs text-white/75">
            <span className="font-semibold text-sun">No config yet.</span> This
            repo doesn&apos;t have a{" "}
            <code className="rounded bg-white/[0.06] px-1 py-0.5">
              .ship/config.yml
            </code>{" "}
            — Save will create one from the current draft. Pick some lanes
            first.
          </p>
        </Card>
      ) : null}
      {drift ? (
        <Card className="border-coral/25 bg-coral/5">
          <p className="text-xs text-coral">
            <span className="font-semibold">HEAD moved.</span> {drift}
          </p>
        </Card>
      ) : null}

      <Card padded={false} className="overflow-hidden">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 px-4 py-3">
          <div>
            <h2 className="font-display text-sm font-bold text-white">
              {repoFullName}
            </h2>
            <p className="text-[11px] text-white/45">
              Editing{" "}
              <code className="rounded bg-white/[0.06] px-1 py-0.5">
                .ship/config.yml
              </code>
              {baseSha ? (
                <>
                  {" "}
                  · sha{" "}
                  <code className="rounded bg-white/[0.04] px-1 py-0.5">
                    {baseSha.slice(0, 7)}
                  </code>
                </>
              ) : null}
            </p>
          </div>
          <div className="text-[11px] text-white/45">
            {hasChanges ? (
              <Badge tone="warn">{changes.length} pending</Badge>
            ) : (
              <Badge tone="neutral">no changes</Badge>
            )}
          </div>
        </header>
        <table className="min-w-full text-sm">
          <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
            <tr>
              <th className="px-4 py-2.5 text-left font-semibold">Lane</th>
              <th className="px-4 py-2.5 text-left font-semibold">Trigger</th>
              <th className="px-4 py-2.5 text-left font-semibold">Schedule / pattern</th>
              <th className="px-4 py-2.5 text-left font-semibold">Enabled</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <LaneRow
                key={row.laneId}
                row={row}
                onChange={(next) =>
                  setDraft((prev) => ({ ...prev, [row.laneId]: next }))
                }
              />
            ))}
          </tbody>
        </table>
      </Card>

      {hasChanges ? (
        <Card>
          <CardHeader
            title="Ready to open a PR"
            subtitle={`${changes.length} change(s) — review + save will open a single-file PR on ${repoFullName}.`}
          />
          <ul className="mt-3 list-disc pl-5 text-xs text-white/70">
            {changes.map((c) => (
              <li key={c.laneId + c.kind}>{describeChange(c)}</li>
            ))}
          </ul>

          <label className="mt-5 block text-xs font-semibold text-white/70">
            Change summary (optional)
            <textarea
              value={summary}
              onChange={(e) => setSummary(e.target.value.slice(0, 1024))}
              rows={2}
              placeholder="Why are we making this change? — surfaces in the PR body."
              className="mt-1 block w-full rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 font-mono text-xs text-white outline-none focus:border-aqua/40"
            />
          </label>

          <div className="mt-5 flex items-center gap-3">
            <button
              type="button"
              onClick={handleSave}
              disabled={disableSave}
              className="rounded-md border border-aqua/40 bg-aqua/10 px-4 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Opening PR…" : "Save → open PR"}
            </button>
            <button
              type="button"
              onClick={() => {
                setDraft(baseline);
                setSummary("");
                setError(null);
              }}
              disabled={!hasChanges || submitting}
              className="rounded-md border border-white/15 bg-white/[0.04] px-4 py-1.5 text-xs font-semibold text-white/70 hover:text-white disabled:opacity-50"
            >
              Discard changes
            </button>
            {cronInvalid ? (
              <span className="text-[11px] text-coral">
                Fix the highlighted cron expression(s) first.
              </span>
            ) : null}
          </div>
          {error ? (
            <p className="mt-3 text-[11px] text-coral">{error}</p>
          ) : null}
        </Card>
      ) : (
        <p className="text-[11px] text-white/45">
          Toggle a lane or tweak a schedule — we&apos;ll surface the
          diff + <em>Save</em> here.
        </p>
      )}

      <div className="space-y-2 text-[11px] text-white/45">
        <p>
          Need a recipe that isn&apos;t here?{" "}
          <Link
            href="/lanes?tab=new"
            className="text-aqua hover:underline"
          >
            Author a custom lane →
          </Link>
        </p>
        <p className="flex flex-wrap items-center gap-2">
          Authored something great?{" "}
          <button
            type="button"
            onClick={() =>
              alert(
                "Coming soon — we'll let you propose your lane to the public Ship library via a fork + upstream PR.",
              )
            }
            className="rounded-full border border-white/15 bg-white/[0.04] px-2.5 py-0.5 text-[10px] font-semibold text-white/70 hover:border-white/30 hover:text-white"
          >
            Propose to public library
          </button>
          <span className="text-white/35">(coming soon)</span>
        </p>
      </div>
    </div>
  );
}

// --------------------------- rows ----------------------------------

type LaneRowModel = {
  laneId: string;
  title: string;
  summary: string;
  triggerLabel: string;
  entry: ApiLaneCatalogEntry | null;
  draft: LaneDraft;
  baseline: LaneDraft;
  origin: LaneDraft["origin"];
};

function LaneRow({
  row,
  onChange,
}: {
  row: LaneRowModel;
  onChange: (next: LaneDraft) => void;
}) {
  const { draft, entry } = row;
  const changed = !draftsEqual(draft, row.baseline);
  const isSchedule = entry?.schedule !== undefined && entry.schedule !== null;
  const cronInvalid =
    draft.enabled && draft.schedule !== null && !isValidCron(draft.schedule);

  return (
    <tr
      className={
        "border-t border-white/5 align-top transition " +
        (changed ? "bg-aqua/[0.03]" : "hover:bg-white/[0.02]")
      }
    >
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <Badge tone="neutral">{row.laneId}</Badge>
          <span className="font-semibold text-white">{row.title}</span>
          {changed ? <Badge tone="warn">modified</Badge> : null}
          {row.origin === "config-only" ? (
            <Badge tone="info">config-only</Badge>
          ) : null}
        </div>
        <p className="mt-1 max-w-[52ch] text-xs text-white/55">
          {row.summary}
        </p>
      </td>
      <td className="px-4 py-3">
        <Badge tone="info">{row.triggerLabel}</Badge>
      </td>
      <td className="px-4 py-3 text-xs">
        {isSchedule && draft.schedule !== null ? (
          <CronWidget
            value={draft.schedule}
            invalid={cronInvalid}
            disabled={!draft.enabled}
            onChange={(next) => onChange({ ...draft, schedule: next })}
          />
        ) : entry?.pattern ? (
          <code className="rounded bg-white/[0.06] px-1.5 py-0.5 text-white/70">
            {entry.pattern}
          </code>
        ) : (
          <span className="text-white/45">—</span>
        )}
      </td>
      <td className="px-4 py-3">
        <button
          type="button"
          onClick={() => onChange({ ...draft, enabled: !draft.enabled })}
          className={
            "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[10px] font-semibold uppercase tracking-wider transition " +
            (draft.enabled
              ? "border-emerald-400/40 bg-emerald-500/15 text-emerald-300"
              : "border-white/15 bg-white/[0.05] text-white/60")
          }
          aria-pressed={draft.enabled}
        >
          <span
            className={
              "h-1.5 w-1.5 rounded-full " +
              (draft.enabled ? "bg-emerald-300" : "bg-white/40")
            }
          />
          {draft.enabled ? "on" : "off"}
        </button>
      </td>
    </tr>
  );
}

// --------------------------- cron widget ---------------------------

function CronWidget({
  value,
  invalid,
  disabled,
  onChange,
}: {
  value: string;
  invalid: boolean;
  disabled: boolean;
  onChange: (next: string) => void;
}) {
  const [custom, setCustom] = useState(() => !CRON_PRESETS.some((p) => p.value === value));
  const preview = invalid ? "invalid cron expression" : humanizeCron(value);
  return (
    <div className="space-y-1.5">
      {!custom ? (
        <select
          value={value}
          onChange={(e) => {
            if (e.target.value === "__custom__") {
              setCustom(true);
              return;
            }
            onChange(e.target.value);
          }}
          disabled={disabled}
          className="w-full min-w-[14rem] rounded-md border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40 disabled:opacity-50"
        >
          {CRON_PRESETS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label} — {p.value}
            </option>
          ))}
          {!CRON_PRESETS.some((p) => p.value === value) ? (
            <option value={value}>{value} (custom)</option>
          ) : null}
          <option value="__custom__">Custom cron…</option>
        </select>
      ) : (
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          placeholder="0 9 * * 1-5"
          className={
            "w-full min-w-[14rem] rounded-md border bg-white/[0.04] px-2 py-1.5 font-mono text-xs text-white outline-none disabled:opacity-50 " +
            (invalid
              ? "border-coral/60 focus:border-coral"
              : "border-white/10 focus:border-aqua/40")
          }
        />
      )}
      <p
        className={
          "text-[10px] " +
          (invalid ? "text-coral" : "text-white/45")
        }
      >
        {preview}
      </p>
      {custom ? (
        <button
          type="button"
          onClick={() => setCustom(false)}
          className="text-[10px] text-aqua hover:underline"
        >
          Back to presets
        </button>
      ) : null}
    </div>
  );
}

// --------------------------- helpers -------------------------------

function buildBaseline(
  catalog: ApiLaneCatalogEntry[],
  config: ApiRepoConfig | null,
  lanes: ApiLane[],
): Record<string, LaneDraft> {
  const baseline: Record<string, LaneDraft> = {};
  const parsedLanes = config?.parsed?.lanes ?? {};

  for (const entry of catalog) {
    const parsed = parsedLanes[entry.kind];
    const inConfig = parsed !== undefined;
    baseline[entry.kind] = {
      enabled: inConfig,
      schedule: entry.schedule
        ? scheduleFromConfig(parsed) ?? entry.schedule
        : null,
      origin: "recipe",
      rawConfig: isPlainObject(parsed) ? parsed : null,
    };
  }

  // Lanes declared in config that we don't have a recipe for (custom
  // or renamed) — include them so we don't silently drop them on
  // save. Rendered as read-only rows.
  for (const laneId of Object.keys(parsedLanes)) {
    if (baseline[laneId]) continue;
    const parsed = parsedLanes[laneId];
    baseline[laneId] = {
      enabled: true,
      schedule: scheduleFromConfig(parsed),
      origin: "config-only",
      rawConfig: isPlainObject(parsed) ? parsed : null,
    };
  }

  // Synced lanes that aren't in our latest config snapshot (stale /
  // not-yet-GC'd rows). Mostly present for visual hint in the UI.
  void lanes;
  return baseline;
}

function scheduleFromConfig(raw: unknown): string | null {
  if (!raw || typeof raw !== "object") return null;
  const sched = (raw as Record<string, unknown>).schedule;
  if (typeof sched === "string") return sched;
  return null;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

type LaneChange =
  | { laneId: string; kind: "enable" }
  | { laneId: string; kind: "disable" }
  | { laneId: string; kind: "schedule"; from: string | null; to: string | null };

function diffDrafts(
  baseline: Record<string, LaneDraft>,
  current: Record<string, LaneDraft>,
): LaneChange[] {
  const out: LaneChange[] = [];
  for (const [laneId, next] of Object.entries(current)) {
    const prev = baseline[laneId];
    if (!prev) continue;
    if (prev.enabled !== next.enabled) {
      out.push({ laneId, kind: next.enabled ? "enable" : "disable" });
    }
    if (next.enabled && prev.schedule !== next.schedule) {
      out.push({
        laneId,
        kind: "schedule",
        from: prev.schedule,
        to: next.schedule,
      });
    }
  }
  return out;
}

function describeChange(c: LaneChange): string {
  if (c.kind === "enable") return `Enable ${c.laneId}`;
  if (c.kind === "disable") return `Disable ${c.laneId}`;
  return `Reschedule ${c.laneId}: ${c.from ?? "(none)"} → ${c.to ?? "(none)"}`;
}

function draftsEqual(a: LaneDraft, b: LaneDraft): boolean {
  return a.enabled === b.enabled && a.schedule === b.schedule;
}

function buildRows(
  catalog: ApiLaneCatalogEntry[],
  baseline: Record<string, LaneDraft>,
  draft: Record<string, LaneDraft>,
): LaneRowModel[] {
  const rows: LaneRowModel[] = [];
  for (const entry of catalog) {
    const d = draft[entry.kind] ?? baseline[entry.kind];
    rows.push({
      laneId: entry.kind,
      title: entry.title,
      summary: entry.summary,
      triggerLabel: triggerLabel(entry),
      entry,
      draft: d,
      baseline: baseline[entry.kind],
      origin: "recipe",
    });
  }
  // Config-only lanes: render after recipes.
  for (const [laneId, base] of Object.entries(baseline)) {
    if (base.origin !== "config-only") continue;
    rows.push({
      laneId,
      title: laneId,
      summary: "Custom lane — declared in config.yml but not in the catalog.",
      triggerLabel: base.schedule ? "schedule" : "custom",
      entry: null,
      draft: draft[laneId] ?? base,
      baseline: base,
      origin: "config-only",
    });
  }
  return rows;
}

function triggerLabel(entry: ApiLaneCatalogEntry): string {
  if (entry.schedule) return "schedule";
  if (entry.event === "pull_request") return "PR";
  if (entry.event === "push") return "push";
  return entry.event ?? "manual";
}

function buildProposalBody({
  catalog,
  draft,
}: {
  catalog: ApiLaneCatalogEntry[];
  draft: Record<string, LaneDraft>;
}): Record<string, Record<string, string | null>> {
  const out: Record<string, Record<string, string | null>> = {};
  const catalogIndex = new Map(catalog.map((e) => [e.kind, e]));

  for (const [laneId, d] of Object.entries(draft)) {
    if (!d.enabled) continue;
    const entry = catalogIndex.get(laneId);
    if (entry) {
      // Known recipe — build trigger from recipe defaults + the
      // edited schedule. The raw config may have idempotency_key /
      // pattern overrides we want to preserve.
      out[laneId] = laneTriggerFromRecipe(entry, d);
      continue;
    }
    // Config-only lane — forward whatever was in the raw config
    // verbatim so we don't silently strip unknown fields.
    const preserved = preserveRawTrigger(d.rawConfig);
    if (preserved) {
      out[laneId] = preserved;
    }
  }
  return out;
}

function laneTriggerFromRecipe(
  entry: ApiLaneCatalogEntry,
  draft: LaneDraft,
): Record<string, string | null> {
  const trigger: Record<string, string | null> = {};
  if (entry.schedule) {
    trigger.schedule = draft.schedule ?? entry.schedule;
  } else if (entry.event) {
    trigger.event = entry.event;
  }
  // Prefer pattern / idempotency_key from raw config if the repo
  // has customised them; otherwise fall back to the recipe default.
  const raw = draft.rawConfig;
  const rawPattern =
    raw && typeof raw.pattern === "string" ? raw.pattern : null;
  const rawIdem =
    raw && typeof raw.idempotency_key === "string" ? raw.idempotency_key : null;
  if (rawPattern ?? entry.pattern) {
    trigger.pattern = rawPattern ?? entry.pattern;
  }
  if (rawIdem ?? entry.idempotency_key) {
    trigger.idempotency_key = rawIdem ?? entry.idempotency_key;
  }
  return trigger;
}

function preserveRawTrigger(
  raw: Record<string, unknown> | null,
): Record<string, string | null> | null {
  if (!raw) return null;
  const out: Record<string, string | null> = {};
  const keys = ["once", "event", "schedule", "pattern", "idempotency_key"] as const;
  for (const k of keys) {
    const v = raw[k];
    if (typeof v === "string") out[k] = v;
  }
  // Need exactly one trigger kind for the backend to accept it.
  const triggerKinds = ["once", "event", "schedule"].filter((k) => out[k]);
  if (triggerKinds.length !== 1) return null;
  return out;
}

// --------------------------- cron ----------------------------------

function isValidCron(expr: string): boolean {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return false;
  // Minimal sanity check — the backend re-validates via cronsim.
  return parts.every((p) => /^[0-9*,\-\/]+(?:[,\-\/][0-9*]+)*$/.test(p));
}

function humanizeCron(expr: string): string {
  const preset = CRON_PRESETS.find((p) => p.value === expr);
  if (preset) return preset.hint;
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return "expected 5 fields: <min> <hour> <dom> <mon> <dow>";
  const [min, hour, dom, mon, dow] = parts;
  if (dom === "*" && mon === "*" && dow === "*") {
    return `Every day at ${pad(hour)}:${pad(min)} UTC`;
  }
  if (dom === "*" && mon === "*" && /^[0-9,\-]+$/.test(dow)) {
    return `On ${formatDow(dow)} at ${pad(hour)}:${pad(min)} UTC`;
  }
  return `cron "${expr}" — UTC`;
}

function pad(s: string): string {
  if (!/^\d+$/.test(s)) return s;
  return s.padStart(2, "0");
}

function formatDow(dow: string): string {
  const names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  if (dow === "1-5") return "weekdays";
  if (dow === "0,6" || dow === "6,0") return "weekends";
  return dow
    .split(",")
    .map((part) => {
      if (part.includes("-")) {
        const [a, b] = part.split("-").map((n) => Number(n));
        if (Number.isFinite(a) && Number.isFinite(b)) {
          return `${names[a] ?? a}–${names[b] ?? b}`;
        }
      }
      const n = Number(part);
      return Number.isFinite(n) ? names[n] ?? part : part;
    })
    .join(", ");
}
