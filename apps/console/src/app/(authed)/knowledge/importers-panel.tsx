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
  ApiImporterDiscoveredItem,
  ApiImporterType,
  ApiWorkspaceImporter,
  ApiWorkspaceImporterIntegration,
} from "@/lib/api/client";

import {
  createImporterAction,
  discoverItemsAction,
  runImporterAction,
} from "./actions";


// Importer types we hide from the operator: local_files only works on
// the engine pod's filesystem and isn't useful in cloud deployments.
const HIDDEN_TYPES = new Set(["local_files"]);


type Props = {
  importers: ApiWorkspaceImporter[];
  importerTypes: ApiImporterType[];
  integrations: ApiWorkspaceImporterIntegration[];
};


export function ImportersPanel({
  importers,
  importerTypes,
  integrations,
}: Props) {
  const [adding, setAdding] = useState(false);
  const visibleTypes = useMemo(
    () => importerTypes.filter((t) => !HIDDEN_TYPES.has(t.type)),
    [importerTypes],
  );
  // Hide the built-in per-workspace S3 importer from the list; it's
  // auto-provisioned and not operator-managed.
  const visibleImporters = useMemo(
    () => importers.filter((i) => i.recipe !== "workspace-s3"),
    [importers],
  );

  if (visibleTypes.length === 0) {
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
          types={visibleTypes}
          integrations={integrations}
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
  integrations,
  onDone,
}: {
  types: ApiImporterType[];
  integrations: ApiWorkspaceImporterIntegration[];
  onDone: () => void;
}) {
  const [typeKey, setTypeKey] = useState<string>(types[0]?.type ?? "");
  const [name, setName] = useState("");
  const [configValues, setConfigValues] = useState<Record<string, string>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  // Default ON whenever the selected type has a workspace integration —
  // the typical case is "user installed GitHub once, wants to import
  // any repo without re-pasting a token".
  const [useWorkspaceIntegration, setUseWorkspaceIntegration] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Discovery state: items returned by the engine + the operator's
  // picks. Active only when the selected type ``supports_discovery``.
  const [discovered, setDiscovered] = useState<
    ApiImporterDiscoveredItem[] | null
  >(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [pending, startTransition] = useTransition();

  const selected = useMemo(
    () => types.find((t) => t.type === typeKey) ?? null,
    [types, typeKey],
  );
  const integration = useMemo(
    () => integrations.find((i) => i.importer_type === typeKey) ?? null,
    [integrations, typeKey],
  );
  const useIntegration = !!integration && useWorkspaceIntegration;
  const needsDiscovery = !!selected?.supports_discovery;
  // Once discovery has surfaced items, we hide the config fields and
  // show only the picker — operators expect the form to advance.
  const inPickerStage = needsDiscovery && discovered !== null;

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
    setDiscovered(null);
    setPicked(new Set());
    setUseWorkspaceIntegration(true);
    setError(null);
  }

  function changeType(next: string) {
    setTypeKey(next);
    reset();
  }

  function validateBaseInputs(): {
    ok: boolean;
    config: Record<string, unknown>;
    message?: string;
  } {
    if (!selected) return { ok: false, config: {}, message: "Pick a type." };
    if (!name.trim()) return { ok: false, config: {}, message: "Name is required." };
    const config = coerceConfig(selected, configValues);
    const required = selected.config_schema?.required ?? [];
    // Skip required-check for fields the workspace integration fills.
    const filledByIntegration = useIntegration
      ? new Set(integration?.provides_config_keys ?? [])
      : new Set<string>();
    const missing = required.filter(
      (k) => isBlank(config[k]) && !filledByIntegration.has(k),
    );
    if (missing.length > 0) {
      return {
        ok: false,
        config,
        message: `Missing required field(s): ${missing.join(", ")}.`,
      };
    }
    if (!useIntegration) {
      const missingSecrets = selected.secret_keys.filter((k) => !secrets[k]);
      if (missingSecrets.length > 0) {
        return {
          ok: false,
          config,
          message: `Missing required secret(s): ${missingSecrets.join(", ")}.`,
        };
      }
    }
    return { ok: true, config };
  }

  function preview() {
    const check = validateBaseInputs();
    if (!check.ok) {
      setError(check.message ?? "Invalid input.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const result = await discoverItemsAction({
        type: selected!.type,
        config: check.config,
        secrets:
          !useIntegration && Object.keys(secrets).length > 0
            ? secrets
            : undefined,
        use_workspace_integration: useIntegration ? true : undefined,
      });
      if (!result.ok) {
        setError(result.message);
        return;
      }
      setDiscovered(result.items);
      // Default to "select all" — operator can uncheck.
      setPicked(new Set(result.items.map((i) => i.id)));
    });
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) return;
    const check = validateBaseInputs();
    if (!check.ok) {
      setError(check.message ?? "Invalid input.");
      return;
    }
    if (needsDiscovery) {
      if (!inPickerStage) {
        // Operator clicked submit before previewing — run preview first.
        preview();
        return;
      }
      if (picked.size === 0) {
        setError("Pick at least one item to import.");
        return;
      }
    }

    const config = needsDiscovery
      ? mergeDiscoveredPatches(
          check.config,
          (discovered ?? []).filter((i) => picked.has(i.id)),
        )
      : check.config;

    setError(null);
    startTransition(async () => {
      const result = await createImporterAction({
        type: selected.type,
        name: name.trim(),
        config,
        secrets:
          !useIntegration && Object.keys(secrets).length > 0
            ? secrets
            : undefined,
        use_workspace_integration: useIntegration ? true : undefined,
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
            disabled={inPickerStage}
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/60 disabled:opacity-60"
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

      {selected && !inPickerStage && (
        <>
          {selected.description && (
            <p className="text-xs text-white/55">{selected.description}</p>
          )}
          {integration && (
            <label className="flex items-start gap-2 rounded border border-aqua/20 bg-aqua/[0.05] p-2 text-xs">
              <input
                type="checkbox"
                checked={useWorkspaceIntegration}
                onChange={(e) =>
                  setUseWorkspaceIntegration(e.target.checked)
                }
                className="mt-0.5"
              />
              <span className="text-white/75">
                Use workspace{" "}
                <span className="text-aqua">{integration.provider}</span>
                {" "}integration
                {integration.account_name
                  ? ` (${integration.account_name})`
                  : ""}
                . Ship resolves the token server-side — the secret never
                leaves the backend.
              </span>
            </label>
          )}
          {Object.entries(selected.config_schema?.properties ?? {})
            // Hide schema entries that match a secret key — those are
            // rendered as dedicated password inputs below (or skipped
            // entirely when the workspace integration fills them).
            // Also hide config keys the chosen integration will fill
            // server-side (e.g. base_url + email for atlassian).
            .filter(
              ([key]) =>
                !selected.secret_keys.includes(key) &&
                !(useIntegration && (integration?.provides_config_keys ?? []).includes(key)),
            )
            .map(([key, prop]) => (
              <SchemaField
                key={key}
                name={key}
                prop={prop}
                required={(selected.config_schema?.required ?? []).includes(key)}
                value={configValues[key] ?? ""}
                onChange={(v) => setConfig(key, v)}
              />
            ))}
          {!useIntegration &&
            selected.secret_keys.map((key) => (
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
          {needsDiscovery && (
            <p className="text-[11px] text-white/45">
              Click <span className="text-white/70">Preview items</span> to
              fetch the list of available items for this source — you&apos;ll
              pick which ones to import before saving.
            </p>
          )}
        </>
      )}

      {inPickerStage && (
        <DiscoveredPicker
          items={discovered ?? []}
          picked={picked}
          onToggle={(id) =>
            setPicked((prev) => {
              const next = new Set(prev);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            })
          }
          onSelectAll={() =>
            setPicked(new Set((discovered ?? []).map((i) => i.id)))
          }
          onClearAll={() => setPicked(new Set())}
          onBack={() => {
            setDiscovered(null);
            setPicked(new Set());
          }}
        />
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
        {needsDiscovery && !inPickerStage && (
          <button
            type="button"
            onClick={preview}
            disabled={pending || !selected}
            className="rounded-full border border-white/15 px-3 py-1 text-xs font-semibold text-white/70 transition hover:border-aqua/40 hover:text-aqua disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending ? "Loading…" : "Preview items"}
          </button>
        )}
        <button
          type="submit"
          disabled={pending || !selected || (inPickerStage && picked.size === 0)}
          className="rounded-full bg-aqua/15 px-3 py-1 text-xs font-semibold text-aqua transition hover:bg-aqua/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending
            ? "Creating…"
            : inPickerStage
              ? `Add ${picked.size} item${picked.size === 1 ? "" : "s"}`
              : needsDiscovery
                ? "Preview & continue"
                : "Add + run"}
        </button>
      </div>
    </form>
  );
}


function DiscoveredPicker({
  items,
  picked,
  onToggle,
  onSelectAll,
  onClearAll,
  onBack,
}: {
  items: ApiImporterDiscoveredItem[];
  picked: Set<string>;
  onToggle: (id: string) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onBack: () => void;
}) {
  return (
    <div className="space-y-3 rounded border border-white/10 bg-black/20 p-3">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-xs text-white/55">
          {items.length} item{items.length === 1 ? "" : "s"} discovered ·{" "}
          {picked.size} selected
        </p>
        <div className="flex items-center gap-2 text-[11px]">
          <button
            type="button"
            onClick={onBack}
            className="text-white/45 hover:text-white"
          >
            ← Back
          </button>
          <button
            type="button"
            onClick={onSelectAll}
            className="text-white/55 hover:text-aqua"
          >
            Select all
          </button>
          <button
            type="button"
            onClick={onClearAll}
            className="text-white/55 hover:text-coral"
          >
            Clear
          </button>
        </div>
      </div>
      {items.length === 0 ? (
        <p className="text-sm text-white/55">
          The engine returned no items for these settings. Go back and
          adjust the config or credentials.
        </p>
      ) : (
        <ul className="max-h-72 space-y-1 overflow-y-auto pr-1 text-sm">
          {items.map((item) => (
            <li key={item.id} className="flex items-start gap-2">
              <input
                id={`pick-${item.id}`}
                type="checkbox"
                checked={picked.has(item.id)}
                onChange={() => onToggle(item.id)}
                className="mt-1"
              />
              <label
                htmlFor={`pick-${item.id}`}
                className="flex-1 cursor-pointer"
              >
                <div className="text-white/85">{item.name || item.id}</div>
                {(item.hint || item.kind) && (
                  <div className="text-[11px] text-white/40">
                    {item.kind}
                    {item.kind && item.hint ? " · " : ""}
                    {item.hint}
                  </div>
                )}
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


// Merge the picked discovered items' ``config_patch`` objects on top
// of the operator's base config. Arrays concatenate (deduped); scalars
// are last-write-wins, with the very last write being any explicit
// value in ``base``.
function mergeDiscoveredPatches(
  base: Record<string, unknown>,
  picks: ApiImporterDiscoveredItem[],
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const item of picks) {
    for (const [k, v] of Object.entries(item.config_patch)) {
      const existing = out[k];
      if (Array.isArray(existing) && Array.isArray(v)) {
        const merged = [...existing];
        for (const el of v) {
          if (!merged.some((x) => deepEqual(x, el))) merged.push(el);
        }
        out[k] = merged;
      } else if (Array.isArray(v)) {
        out[k] = [...v];
      } else {
        out[k] = v;
      }
    }
  }
  // Operator's explicit base values take final priority.
  return { ...out, ...base };
}


function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || !a || !b) return false;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
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
