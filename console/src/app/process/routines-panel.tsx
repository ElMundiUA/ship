"use client";

import { useEffect, useMemo, useState } from "react";

import { Card, CardHeader, Switch } from "@/components/ui";
import type { ApiProcess, ApiProcessRoutine, ApiRepoConfig } from "@/lib/api/client";
import { deriveDescriptionFromPrompt } from "@/lib/derive-routine-description";
import { formatNextRun } from "@/lib/cron-next";
import {
  buildUtcCadenceFromSpec,
  defaultScheduleSpec,
  parseScheduleFromYaml,
} from "@/lib/routine-schedule-spec";
import { processConfigFromApiProcess } from "./process-config";
import { ProcessConfigProposalFields } from "./process-config-proposal-fields";
import { ProcessReviewSummary, processChangeSummary } from "./process-review-summary";
import { BASE_SPECIALIST_CATALOG } from "./specialist-catalog";
import { BUILTIN_ROUTINE_CATALOG } from "./routine-catalog";
import { RoutineScheduleForm } from "./routine-schedule-form";

const SPECIALIST_OPTIONS = BASE_SPECIALIST_CATALOG.map((s) => ({
  id: s.id,
  name: s.name,
}));

function normalizeRoutine(r: ApiProcessRoutine): ApiProcessRoutine {
  const prompt = (r.prompt ?? r.instructions ?? "").trim();
  const spec =
    r.schedule_spec ??
    parseScheduleFromYaml({} as Record<string, unknown>, r.schedule);
  return {
    ...r,
    prompt,
    schedule_spec: spec,
  };
}

function cardDescription(r: ApiProcessRoutine): string {
  if (r.description?.trim()) return r.description.trim();
  return deriveDescriptionFromPrompt(r.prompt ?? "");
}

export function RoutinesPanel({
  workspaceId,
  process,
  repoId,
  config,
}: {
  workspaceId: string;
  process: ApiProcess;
  repoId?: string;
  config: ApiRepoConfig | null;
}) {
  const [routines, setRoutines] = useState<ApiProcessRoutine[]>(() =>
    process.routines.map(normalizeRoutine),
  );
  const [editingId, setEditingId] = useState<string | null>(null);
  const [knownZones, setKnownZones] = useState<string[]>([]);
  const [draftText, setDraftText] = useState("");
  const [draftWarning, setDraftWarning] = useState<string | null>(null);

  useEffect(() => {
    setRoutines(process.routines.map(normalizeRoutine));
  }, [process.routines]);

  useEffect(() => {
    try {
      const z = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (z) setKnownZones((prev) => (prev.includes(z) ? prev : [z, ...prev]));
    } catch {
      /* ignore */
    }
  }, []);

  const processDraft = useMemo<ApiProcess>(
    () => ({ ...process, routines }),
    [process, routines],
  );
  const processConfig = useMemo(
    () => processConfigFromApiProcess(processDraft),
    [processDraft],
  );
  const initialConfig = useMemo(
    () => processConfigFromApiProcess({
      ...process,
      routines: process.routines.map(normalizeRoutine),
    }),
    [process],
  );
  const initialReviewProcess = useMemo<ApiProcess>(
    () => ({
      ...process,
      routines: process.routines.map(normalizeRoutine),
    }),
    [process],
  );
  const dirty = JSON.stringify(processConfig) !== JSON.stringify(initialConfig);
  const changeSummary = processChangeSummary(initialReviewProcess, processDraft, [
    ...(dirty ? ["Standalone routines changed"] : []),
  ]);
  const legacyLaneCount = legacyRoutineLaneCount(config);

  function addFromCatalog(catalogId: string) {
    if (routines.some((r) => r.id === catalogId)) return;
    const row = BUILTIN_ROUTINE_CATALOG.find((c) => c.id === catalogId);
    if (!row) return;
    const sp = SPECIALIST_OPTIONS.find((s) => s.id === "devops_platform") ?? SPECIALIST_OPTIONS[0];
    const tz =
      (typeof window !== "undefined" &&
        Intl.DateTimeFormat().resolvedOptions().timeZone) ||
      "UTC";
    const spec = {
      v: 1 as const,
      time_zone: tz,
      mode: "expert_utc" as const,
      cron_utc: row.defaultCron,
    };
    setRoutines((current) => [
      ...current,
      normalizeRoutine({
        id: row.id,
        name: row.name,
        specialist_id: sp.id,
        specialist_name: sp.name,
        schedule: row.defaultCron,
        prompt: row.prompt,
        instructions: row.prompt,
        last_run: null,
        status: null,
        enabled: true,
        description: "",
        schedule_spec: spec,
      }),
    ]);
  }

  function addPlain(nameRaw: string) {
    const name = nameRaw.trim();
    if (!name) return;
    const base = name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 48) || "routine";
    let id = `custom_${base}`;
    let n = 2;
    while (routines.some((r) => r.id === id)) {
      id = `custom_${base}_${n}`;
      n += 1;
    }
    const sp = SPECIALIST_OPTIONS.find((s) => s.id === "devops_platform") ?? SPECIALIST_OPTIONS[0];
    const spec = defaultScheduleSpec();
    const cad = buildUtcCadenceFromSpec(spec) ?? "0 9 * * 1-5";
    setRoutines((current) => [
      ...current,
      normalizeRoutine({
        id,
        name,
        specialist_id: sp.id,
        specialist_name: sp.name,
        schedule: cad,
        prompt: "",
        instructions: "",
        last_run: null,
        status: null,
        enabled: true,
        description: "",
        schedule_spec: spec,
      }),
    ]);
  }

  function draftRoutineFromDescription(descriptionRaw: string) {
    const description = descriptionRaw.trim();
    if (!description) return;
    if (looksLikeTicketMutation(description)) {
      setDraftWarning(
        "This sounds like ticket-picking or ticket mutation. Model it as a scheduled process/subprocess, not a routine.",
      );
      return;
    }
    setDraftWarning(null);
    const lower = description.toLowerCase();
    const specialist =
      lower.includes("standup") || lower.includes("commitment")
        ? optionById("product_manager")
        : lower.includes("tech") || lower.includes("architecture") || lower.includes("debt")
          ? optionById("technical_architect")
          : lower.includes("security")
            ? optionById("security_engineer")
            : optionById("devops_platform");
    const spec = lower.includes("weekly")
      ? {
          ...defaultScheduleSpec(),
          mode: "weekly" as const,
          weekday: 1,
          time: "09:00",
        }
      : defaultScheduleSpec();
    const cad = buildUtcCadenceFromSpec(spec) ?? "0 9 * * 1-5";
    const base = description
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 42) || "routine";
    let id = `ai_${base}`;
    let n = 2;
    while (routines.some((r) => r.id === id)) {
      id = `ai_${base}_${n}`;
      n += 1;
    }
    const name = titleCase(description.replace(/[.!?]+$/g, "")).slice(0, 80);
    const prompt = [
      description,
      "",
      "Use read-only workspace context only: recent commits, pull requests, runs, incidents, and knowledge from the configured lookback window.",
      "Do not pick, claim, move, or mutate tracker tickets. If action is needed, create a digest item with recommendations.",
    ].join("\n");
    setRoutines((current) => [
      ...current,
      normalizeRoutine({
        id,
        name,
        specialist_id: specialist.id,
        specialist_name: specialist.name,
        schedule: cad,
        prompt,
        instructions: prompt,
        last_run: null,
        status: null,
        enabled: true,
        description,
        trigger: { type: "schedule", cron: cad, window: "30m", catchup: "latest" },
        scope: { kind: "workspace_activity", lookback: "24h", read_only: true },
        output: { destination: "inbox", format: "digest" },
        prompt_record: {
          id: `routine_prompt_${id}`,
          version: 1,
          source: "ai_draft",
          assumptions: [
            "Use read-only workspace activity from the configured lookback window.",
            "Send the result to Ship Inbox as a digest.",
            "Do not pick, claim, move, or mutate tracker tickets.",
          ],
        },
        schedule_spec: spec,
      }),
    ]);
    setDraftText("");
  }

  function patchRoutine(
    id: string,
    patch: Partial<ApiProcessRoutine>,
  ) {
    setRoutines((current) =>
      current.map((r) => (r.id === id ? normalizeRoutine({ ...r, ...patch }) : r)),
    );
  }

  function removeRoutine(id: string) {
    setRoutines((current) => current.filter((r) => r.id !== id));
  }

  return (
    <Card>
      <div className="space-y-4 p-1">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <CardHeader
            className="p-0"
            title="Routines"
            subtitle="Recurring work. The cadence in the file is stored as a UTC 5-field cron; the schedule builder uses your time zone. Changes go to .ship/config.yml via PR."
          />
          <form
            action="/api/process/config-propose"
            method="post"
            className="flex flex-wrap items-center gap-2"
          >
            <ProcessConfigProposalFields
              workspaceId={workspaceId}
              repoId={repoId}
              config={config}
              processConfig={processConfig}
              changeSummary={changeSummary}
            />
            <button
              type="submit"
              disabled={!repoId || !dirty}
              className="h-9 whitespace-nowrap rounded-full border border-aqua/30 bg-aqua/10 px-4 text-xs font-bold text-aqua transition hover:bg-aqua/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.05] disabled:text-white/35"
            >
              Publish changes
            </button>
          </form>
        </div>

        <ProcessReviewSummary
          initial={initialReviewProcess}
          draft={processDraft}
          changedAreas={dirty ? ["Standalone routines changed"] : []}
        />

        {legacyLaneCount > 0 ? (
          <div className="rounded-xl border border-amber-300/25 bg-amber-300/[0.06] px-3 py-2 text-xs leading-relaxed text-amber-100/90">
            This repo still has {legacyLaneCount} legacy lane automation
            {legacyLaneCount === 1 ? "" : "s"}. They remain readable for
            compatibility, but this editor creates and updates only
            `process.routines`.
          </div>
        ) : null}

        <div className="rounded-xl border border-aqua/20 bg-aqua/[0.045] p-3">
          <div className="text-[10px] font-bold uppercase tracking-widest text-aqua/75">
            Create with AI
          </div>
          <form
            className="mt-2 flex flex-col gap-2 md:flex-row"
            onSubmit={(e) => {
              e.preventDefault();
              draftRoutineFromDescription(draftText);
            }}
          >
            <textarea
              value={draftText}
              onChange={(e) => setDraftText(e.target.value)}
              rows={2}
              placeholder="Every morning, check yesterday's commitments and send me a digest."
              className="min-h-16 flex-1 resize-none rounded-xl border border-white/10 bg-ink/60 px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
            />
            <button
              type="submit"
              className="rounded-full border border-aqua/30 bg-aqua/10 px-4 py-2 text-xs font-bold text-aqua transition hover:bg-aqua/15"
            >
              Draft routine
            </button>
          </form>
          <p className="mt-2 text-xs leading-relaxed text-white/45">
            The draft creates `process.routines` entries with read-only scope and inbox output.
          </p>
          {draftWarning ? (
            <p className="mt-2 rounded-lg border border-coral/25 bg-coral/[0.08] px-3 py-2 text-xs text-coral/90">
              {draftWarning}
            </p>
          ) : null}
        </div>

        <div className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.02] p-3 sm:flex-row sm:flex-wrap sm:items-end">
          <label className="min-w-0 flex-1 block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
              Seeded examples
            </span>
            <select
              className="w-full rounded-xl border border-white/10 bg-ink px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
              defaultValue=""
              onChange={(e) => {
                const v = e.target.value;
                e.target.value = "";
                if (v) addFromCatalog(v);
              }}
            >
              <option value="">Choose starter…</option>
              {BUILTIN_ROUTINE_CATALOG.map((c) => (
                <option
                  key={c.id}
                  value={c.id}
                  disabled={routines.some((r) => r.id === c.id)}
                >
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <form
            className="flex flex-1 flex-col gap-2 sm:flex-row sm:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              const fd = new FormData(e.currentTarget);
              addPlain((fd.get("plainName") as string) || "");
              e.currentTarget.reset();
            }}
          >
            <label className="min-w-0 flex-1 block">
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
                Plain name
              </span>
              <input
                name="plainName"
                placeholder="e.g. Weekly license audit"
                className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
              />
            </label>
            <button
              type="submit"
              className="rounded-full border border-white/10 bg-white/[0.06] px-4 py-2 text-xs font-bold text-white/80 transition hover:border-white/20"
            >
              Add
            </button>
          </form>
        </div>

        {routines.length === 0 ? (
          <p className="text-sm text-white/50">No routines in this process yet.</p>
        ) : (
          <ul className="space-y-3">
            {routines.map((routine) => {
              const desc = cardDescription(routine);
              return (
                <li
                  key={routine.id}
                  className="rounded-xl border border-white/10 bg-white/[0.035] p-3"
                >
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-semibold text-white">{routine.name}</div>
                      {desc ? (
                        <p className="mt-1 text-xs leading-relaxed text-white/50">
                          {desc}
                        </p>
                      ) : null}
                      <div className="mt-2 grid gap-1 text-xs text-white/45 sm:grid-cols-2">
                        <div>
                          Schedule:{" "}
                          <span className="text-white/70">
                            {routine.schedule ?? "—"}
                          </span>
                        </div>
                        <div>
                          Last status:{" "}
                          <span className="text-white/70">{routine.status ?? "—"}</span>
                        </div>
                        <div>
                          Last run:{" "}
                          <span className="text-white/70">
                            {formatRunTime(routine.last_run)}
                          </span>
                        </div>
                        <div>
                          Next (est. UTC):{" "}
                          <span className="text-white/70">
                            {formatNextRun(routine.schedule)}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
                      <div className="flex h-8 items-center gap-2 rounded-full border border-white/10 bg-white/[0.025] pl-3 pr-1.5 text-xs text-white/55">
                        <span>Enabled</span>
                        <Switch
                          checked={routine.enabled !== false}
                          onChange={(next) => patchRoutine(routine.id, { enabled: next })}
                          aria-label={`Enable ${routine.name}`}
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          setEditingId(editingId === routine.id ? null : routine.id)
                        }
                        className="h-8 rounded-full border border-white/10 bg-white/[0.04] px-3 text-xs font-semibold text-aqua/90 transition hover:border-aqua/30 hover:bg-aqua/[0.06]"
                      >
                        {editingId === routine.id ? "Close" : "Edit"}
                      </button>
                      <button
                        type="button"
                        onClick={() => removeRoutine(routine.id)}
                        className="h-8 rounded-full border border-coral/25 px-3 text-xs font-semibold text-coral/90 transition hover:bg-coral/10"
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                  {editingId === routine.id ? (
                    <RoutineEditForm
                      routine={routine}
                      knownZones={knownZones}
                      onChange={(patch) => patchRoutine(routine.id, patch)}
                    />
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}

function formatRunTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function RoutineEditForm({
  routine,
  onChange,
  knownZones,
}: {
  routine: ApiProcessRoutine;
  onChange: (patch: Partial<ApiProcessRoutine>) => void;
  knownZones: string[];
}) {
  const spec = routine.schedule_spec ?? defaultScheduleSpec();
  return (
    <div className="mt-3 space-y-3 border-t border-white/10 pt-3">
      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
          Display name
        </span>
        <input
          value={routine.name}
          onChange={(e) => onChange({ name: e.target.value })}
          className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
        />
      </label>
      <div className="grid gap-3 md:grid-cols-2">
        <ReadOnlyContract
          title="Scope"
          value={routine.scope ?? { kind: "workspace_activity", read_only: true }}
        />
        <ReadOnlyContract
          title="Output"
          value={routine.output ?? { destination: "inbox", format: "digest" }}
        />
      </div>
      {routine.prompt_record ? (
        <ReadOnlyContract title="Prompt version" value={routine.prompt_record} />
      ) : null}
      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
          Description
        </span>
        <textarea
          value={routine.description ?? ""}
          onChange={(e) => onChange({ description: e.target.value })}
          rows={2}
          placeholder="Optional. If empty, it is auto-filled from the prompt when you save the PR."
          className="w-full resize-none rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
        />
      </label>
      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
          Prompt
        </span>
        <textarea
          value={routine.prompt ?? ""}
          onChange={(e) => onChange({ prompt: e.target.value, instructions: e.target.value })}
          rows={4}
          placeholder="Instructions for the agent…"
          className="w-full resize-none rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
        />
      </label>
      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
          Specialist
        </span>
        <select
          value={routine.specialist_id}
          onChange={(e) => {
            const o = SPECIALIST_OPTIONS.find((s) => s.id === e.target.value);
            if (o) onChange({ specialist_id: o.id, specialist_name: o.name });
          }}
          className="w-full rounded-xl border border-white/10 bg-ink px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
        >
          {SPECIALIST_OPTIONS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <div>
        <span className="mb-2 block text-[10px] font-bold uppercase tracking-widest text-white/45">
          Schedule
        </span>
        <RoutineScheduleForm
          spec={spec}
          onChange={(next) => {
            const cad = buildUtcCadenceFromSpec(next) ?? routine.schedule;
            onChange({
              schedule_spec: { ...next, v: 1 },
              schedule: cad,
            });
          }}
          allZones={knownZones}
        />
      </div>
      {routine.schedule ? (
        <p className="text-[11px] text-white/40">
          Resulting <code className="font-mono">cadence</code>:{" "}
          <code className="font-mono text-white/60">{routine.schedule}</code>
        </p>
      ) : null}
    </div>
  );
}

function optionById(id: string) {
  return (
    SPECIALIST_OPTIONS.find((option) => option.id === id) ??
    SPECIALIST_OPTIONS[0] ?? { id: "devops_platform", name: "DevOps/platform" }
  );
}

function looksLikeTicketMutation(value: string) {
  return /\b(pick|claim|move|transition|work on next|take next)\b.*\b(ticket|issue|task)\b/i.test(value);
}

function titleCase(value: string) {
  return value.replace(/\b\w/g, (char) => char.toUpperCase());
}

function ReadOnlyContract({
  title,
  value,
}: {
  title: string;
  value: Record<string, unknown>;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
        {title}
      </div>
      <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-black/20 p-2 text-[11px] leading-relaxed text-white/55">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function legacyRoutineLaneCount(config: ApiRepoConfig | null) {
  const lanes = config?.parsed?.lanes;
  if (!lanes || typeof lanes !== "object" || Array.isArray(lanes)) return 0;
  return Object.keys(lanes).length;
}
