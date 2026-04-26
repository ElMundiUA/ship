"use client";

import { useEffect, useMemo, useState } from "react";

import { Card, CardHeader } from "@/components/ui";
import type { ApiProcess, ApiProcessRoutine, ApiRepoConfig } from "@/lib/api/client";
import { formatNextRun } from "@/lib/cron-next";
import { processConfigFromApiProcess } from "./process-config";
import { ProcessConfigProposalFields } from "./process-config-proposal-fields";
import { BASE_SPECIALIST_CATALOG } from "./specialist-catalog";
import { BUILTIN_ROUTINE_CATALOG, CRON_PRESETS } from "./routine-catalog";

const SPECIALIST_OPTIONS = BASE_SPECIALIST_CATALOG.map((s) => ({
  id: s.id,
  name: s.name,
}));

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
  const [routines, setRoutines] = useState<ApiProcessRoutine[]>(process.routines);
  const [editingId, setEditingId] = useState<string | null>(null);

  useEffect(() => {
    setRoutines(process.routines);
  }, [process.routines]);

  const processDraft = useMemo<ApiProcess>(
    () => ({ ...process, routines }),
    [process, routines],
  );
  const processConfig = useMemo(
    () => processConfigFromApiProcess(processDraft),
    [processDraft],
  );
  const initialConfig = useMemo(
    () => processConfigFromApiProcess(process),
    [process],
  );
  const dirty = JSON.stringify(processConfig) !== JSON.stringify(initialConfig);

  function addFromCatalog(catalogId: string) {
    if (routines.some((r) => r.id === catalogId)) return;
    const row = BUILTIN_ROUTINE_CATALOG.find((c) => c.id === catalogId);
    if (!row) return;
    const sp = SPECIALIST_OPTIONS.find((s) => s.id === "devops_platform") ?? SPECIALIST_OPTIONS[0];
    setRoutines((current) => [
      ...current,
      {
        id: row.id,
        name: row.name,
        specialist_id: sp.id,
        specialist_name: sp.name,
        schedule: row.defaultCron,
        instructions: row.description,
        last_run: null,
        status: null,
        enabled: true,
        description: row.description,
      },
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
    setRoutines((current) => [
      ...current,
      {
        id,
        name,
        specialist_id: sp.id,
        specialist_name: sp.name,
        schedule: "0 9 * * 1-5",
        instructions: "",
        last_run: null,
        status: null,
        enabled: true,
        description: "",
      },
    ]);
  }

  function patchRoutine(
    id: string,
    patch: Partial<ApiProcessRoutine>,
  ) {
    setRoutines((current) =>
      current.map((r) => (r.id === id ? { ...r, ...patch } : r)),
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
            subtitle="Recurring work on a cron or lane trigger. Changes go to .ship/config.yml via PR."
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
            {routines.map((routine) => (
              <li
                key={routine.id}
                className="rounded-xl border border-white/10 bg-white/[0.035] p-3"
              >
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-white">{routine.name}</div>
                    {routine.description ? (
                      <p className="mt-1 text-xs leading-relaxed text-white/50">
                        {routine.description}
                      </p>
                    ) : null}
                    <div className="mt-2 grid gap-1 text-xs text-white/45 sm:grid-cols-2">
                      <div>
                        Schedule:{" "}
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
                        Next run (est.):{" "}
                        <span className="text-white/70">
                          {formatNextRun(routine.schedule)}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 sm:flex-col sm:items-end">
                    <label className="flex cursor-pointer items-center gap-2 text-xs text-white/70">
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 rounded border-white/20 bg-white/[0.04] accent-aqua"
                        checked={routine.enabled !== false}
                        onChange={(e) =>
                          patchRoutine(routine.id, { enabled: e.target.checked })
                        }
                      />
                      <span>Enabled</span>
                    </label>
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
                    onChange={(patch) => patchRoutine(routine.id, patch)}
                  />
                ) : null}
              </li>
            ))}
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
}: {
  routine: ApiProcessRoutine;
  onChange: (patch: Partial<ApiProcessRoutine>) => void;
}) {
  const [customCron, setCustomCron] = useState(routine.schedule ?? "");
  useEffect(() => {
    setCustomCron(routine.schedule ?? "");
  }, [routine.id, routine.schedule]);
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
          rows={3}
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
      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
          Cron (UTC, 5 fields)
        </span>
        <select
          className="mb-2 w-full rounded-xl border border-white/10 bg-ink px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
          value=""
          onChange={(e) => {
            const v = e.target.value;
            if (v) {
              setCustomCron(v);
              onChange({ schedule: v });
            }
          }}
        >
          <option value="">Load preset…</option>
          {CRON_PRESETS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
        <input
          value={customCron}
          onChange={(e) => {
            setCustomCron(e.target.value);
            onChange({ schedule: e.target.value || null });
          }}
          placeholder="0 9 * * 1-5"
          className="w-full rounded-xl border border-white/10 bg-white/[0.04] font-mono px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
        />
      </label>
    </div>
  );
}
