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
  const dirty = JSON.stringify(processConfig) !== JSON.stringify(initialConfig);

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
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
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
            />
            <button
              type="submit"
              disabled={!repoId || !dirty}
              className="rounded-full border border-aqua/30 bg-aqua/10 px-4 py-2 text-xs font-bold text-aqua transition hover:bg-aqua/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.05] disabled:text-white/35"
            >
              Open config PR
            </button>
          </form>
        </div>

        <div className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.02] p-3 sm:flex-row sm:flex-wrap sm:items-end">
          <label className="min-w-0 flex-1 block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
              Add from catalog
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
              <option value="">Choose preset…</option>
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
                          Cadence (UTC):{" "}
                          <span className="font-mono text-white/70">
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
                    <div className="flex flex-wrap items-center gap-3 sm:flex-col sm:items-end">
                      <div className="flex items-center gap-2 text-xs text-white/55">
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
                        className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs font-semibold text-aqua/90 hover:border-aqua/30"
                      >
                        {editingId === routine.id ? "Close" : "Edit"}
                      </button>
                      <button
                        type="button"
                        onClick={() => removeRoutine(routine.id)}
                        className="rounded-full border border-coral/25 px-3 py-1 text-xs font-semibold text-coral/90 hover:bg-coral/10"
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
