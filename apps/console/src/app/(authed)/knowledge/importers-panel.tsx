"use client";

/**
 * Workspace importer management panel.
 *
 * - Shows existing importers (name, type, status, last run, error).
 * - "Add import source" form: pick a type from ``importerTypes``,
 *   the form below renders driven by that type's ``config_schema`` +
 *   ``secret_keys``. Submitting creates the importer via Ship's
 *   proxy + Lighthouse's ``/v1/importers/``, then immediately
 *   triggers an on-demand run so the corpus starts populating.
 */

import { useMemo, useState, useTransition } from "react";

import { cn } from "@/lib/cn";
import type {
  ApiImporterType,
  ApiWorkspaceImporter,
} from "@/lib/api/client";

import { createImporterAction, runImporterAction } from "./actions";


type Props = {
  importers: ApiWorkspaceImporter[];
  importerTypes: ApiImporterType[];
};


export function ImportersPanel({ importers, importerTypes }: Props) {
  const [adding, setAdding] = useState(false);
  // Hide the built-in per-workspace S3 importer from the list; it's
  // auto-provisioned and not operator-managed.
  const visibleImporters = useMemo(
    () => importers.filter((i) => i.recipe !== "workspace-s3"),
    [importers],
  );

  if (importerTypes.length === 0) {
    // Lighthouse unreachable or unconfigured — render nothing.
    return null;
  }

  return (
    <section className="space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-[11px] font-bold uppercase tracking-[0.22em] text-aqua/75">
          Import sources
        </h2>
        <button
          type="button"
          onClick={() => setAdding((v) => !v)}
          className={cn(
            "text-xs transition",
            adding ? "text-aqua" : "text-white/55 hover:text-white",
          )}
        >
          {adding ? "Close" : "Add import source"}
        </button>
      </div>

      {adding && (
        <AddImporterForm
          types={importerTypes}
          onDone={() => setAdding(false)}
        />
      )}

      {visibleImporters.length === 0 ? (
        <p className="text-sm text-white/55">
          No import sources yet. Use “Add import source” to connect a
          sitemap, RSS feed, GitHub repo, or any other engine-supported
          source.
        </p>
      ) : (
        <ul className="divide-y divide-white/5">
          {visibleImporters.map((imp) => (
            <ImporterRow key={imp.id} importer={imp} />
          ))}
        </ul>
      )}
    </section>
  );
}


function ImporterRow({ importer }: { importer: ApiWorkspaceImporter }) {
  const [pending, startTransition] = useTransition();
  const [feedback, setFeedback] = useState<string | null>(null);

  function trigger() {
    setFeedback(null);
    startTransition(async () => {
      const result = await runImporterAction(importer.id);
      setFeedback(
        result.ok ? "Run queued — refresh in a minute." : result.message,
      );
    });
  }

  return (
    <li className="grid grid-cols-[1fr_auto_auto] items-center gap-3 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-white">
            {importer.name}
          </span>
          <span className="text-[11px] uppercase tracking-widest text-white/40">
            {importer.type}
          </span>
        </div>
        {importer.last_error && (
          <p className="mt-0.5 line-clamp-1 text-[11px] text-coral">
            {importer.last_error}
          </p>
        )}
        {feedback && (
          <p className="mt-0.5 text-[11px] text-white/55">{feedback}</p>
        )}
      </div>
      <div className="flex flex-col items-end text-[11px] text-white/45">
        <span className={statusColor(importer.status)}>{importer.status}</span>
        <span>
          {importer.last_run_at ? relativeDate(importer.last_run_at) : "never"}
        </span>
      </div>
      <button
        type="button"
        disabled={pending}
        onClick={trigger}
        className="rounded-full border border-white/10 px-2.5 py-1 text-[11px] text-white/55 transition hover:border-aqua/40 hover:text-aqua disabled:cursor-not-allowed disabled:opacity-50"
      >
        {pending ? "Running…" : "Run now"}
      </button>
    </li>
  );
}


function AddImporterForm({
  types,
  onDone,
}: {
  types: ApiImporterType[];
  onDone: () => void;
}) {
  const [typeKey, setTypeKey] = useState<string>(types[0]?.type ?? "");
  const [name, setName] = useState("");
  const [configValues, setConfigValues] = useState<Record<string, string>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const selected = useMemo(
    () => types.find((t) => t.type === typeKey) ?? null,
    [types, typeKey],
  );

  function setConfig(key: string, value: string) {
    setConfigValues((prev) => ({ ...prev, [key]: value }));
  }

  function setSecret(key: string, value: string) {
    setSecrets((prev) => ({ ...prev, [key]: value }));
  }

  function reset() {
    setConfigValues({});
    setSecrets({});
    setName("");
    setError(null);
  }

  function changeType(next: string) {
    setTypeKey(next);
    reset();
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) return;
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    const config = coerceConfig(selected, configValues);
    const required = selected.config_schema?.required ?? [];
    const missing = required.filter((k) => isBlank(config[k]));
    if (missing.length > 0) {
      setError(`Missing required field(s): ${missing.join(", ")}.`);
      return;
    }
    const missingSecrets = selected.secret_keys.filter((k) => !secrets[k]);
    if (missingSecrets.length > 0) {
      setError(
        `Missing required secret(s): ${missingSecrets.join(", ")}.`,
      );
      return;
    }

    setError(null);
    startTransition(async () => {
      const result = await createImporterAction({
        type: selected.type,
        name: name.trim(),
        config,
        secrets: Object.keys(secrets).length > 0 ? secrets : undefined,
      });
      if (!result.ok) {
        setError(result.message);
        return;
      }
      // Trigger a run immediately so the corpus starts populating.
      await runImporterAction(result.importer.id);
      reset();
      onDone();
    });
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-4 rounded-lg border border-aqua/30 bg-white/[0.02] p-4"
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label className="space-y-1 text-xs">
          <span className="text-white/60">Type</span>
          <select
            value={typeKey}
            onChange={(e) => changeType(e.target.value)}
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/60"
          >
            {types.map((t) => (
              <option key={t.type} value={t.type}>
                {t.display_name} ({t.type})
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-white/60">Name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Python docs"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/60"
          />
        </label>
      </div>

      {selected && (
        <>
          {selected.description && (
            <p className="text-xs text-white/55">{selected.description}</p>
          )}
          {Object.entries(selected.config_schema?.properties ?? {}).map(
            ([key, prop]) => (
              <SchemaField
                key={key}
                name={key}
                prop={prop}
                required={(selected.config_schema?.required ?? []).includes(key)}
                value={configValues[key] ?? ""}
                onChange={(v) => setConfig(key, v)}
              />
            ),
          )}
          {selected.secret_keys.map((key) => (
            <label key={key} className="block space-y-1 text-xs">
              <span className="text-white/60">{key} (secret)</span>
              <input
                type="password"
                value={secrets[key] ?? ""}
                onChange={(e) => setSecret(key, e.target.value)}
                autoComplete="off"
                className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/60"
              />
            </label>
          ))}
        </>
      )}

      {error && <p className="text-xs text-coral">{error}</p>}

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => {
            reset();
            onDone();
          }}
          className="text-xs text-white/55 hover:text-white"
          disabled={pending}
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={pending || !selected}
          className="rounded-full bg-aqua/15 px-3 py-1 text-xs font-semibold text-aqua transition hover:bg-aqua/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending ? "Creating…" : "Add + run"}
        </button>
      </div>
    </form>
  );
}


function SchemaField({
  name,
  prop,
  required,
  value,
  onChange,
}: {
  name: string;
  prop: NonNullable<
    ApiImporterType["config_schema"]["properties"]
  >[string];
  required: boolean;
  value: string;
  onChange: (v: string) => void;
}) {
  const label = prop.title || name;
  const hint = prop.description;
  const isTextarea = prop.format === "textarea";
  const isUri = prop.format === "uri";
  const isInt = prop.type === "integer" || prop.type === "number";
  const isBool = prop.type === "boolean";

  if (isBool) {
    return (
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={value === "true"}
          onChange={(e) => onChange(e.target.checked ? "true" : "false")}
        />
        <span className="text-white/70">
          {label}
          {required && <span className="text-coral"> *</span>}
        </span>
      </label>
    );
  }

  return (
    <label className="block space-y-1 text-xs">
      <span className="text-white/60">
        {label}
        {required && <span className="text-coral"> *</span>}
      </span>
      {isTextarea ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={4}
          placeholder={hint ?? ""}
          className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/60"
        />
      ) : (
        <input
          type={isInt ? "number" : isUri ? "url" : "text"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={hint ?? ""}
          className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/60"
        />
      )}
      {hint && <span className="block text-[11px] text-white/40">{hint}</span>}
    </label>
  );
}


// Schema-driven coercion: strings stay strings; integer/number fields
// become numbers; booleans become booleans. Empty strings drop out so
// the engine sees only the keys the operator actually filled.
function coerceConfig(
  type: ApiImporterType,
  raw: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const props = type.config_schema?.properties ?? {};
  for (const [key, prop] of Object.entries(props)) {
    const v = raw[key];
    if (v === undefined || v === "") continue;
    if (prop.type === "integer") out[key] = Number.parseInt(v, 10);
    else if (prop.type === "number") out[key] = Number.parseFloat(v);
    else if (prop.type === "boolean") out[key] = v === "true";
    else out[key] = v;
  }
  return out;
}


function isBlank(v: unknown): boolean {
  return v === undefined || v === null || v === "";
}


function statusColor(status: string): string {
  if (status === "error") return "text-coral";
  if (status === "running") return "text-aqua";
  if (status === "ready" || status === "idle") return "text-emerald-400";
  return "text-white/55";
}


function relativeDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = Date.parse(value);
  if (Number.isNaN(date)) return "—";
  const diff = Date.now() - date;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < hour) return `${Math.max(1, Math.round(diff / minute))}m ago`;
  if (diff < day) return `${Math.round(diff / hour)}h ago`;
  if (diff < 30 * day) return `${Math.round(diff / day)}d ago`;
  return new Date(date).toLocaleDateString();
}
