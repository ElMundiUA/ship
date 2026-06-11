"use client";

/**
 * Generic per-workspace config-scope renderer.
 *
 * Drives off the JSONSchema returned by ``GET /v1/workspaces/{ws}/config/{scope}``
 * (via the generic session proxy — ELS-236 retired the bespoke BFF route)
 * so adding a new ``ConfigScope`` server-side automatically shows up
 * in the UI without an FE change. The form-renderer covers the three
 * shapes the registry currently uses:
 *
 *   * ``"string"`` with ``enum`` → ``<select>``
 *   * ``["string","null"]``      → ``<input type="text">`` + clear button
 *   * ``"object"`` with bool props → set of checkboxes
 *
 * Anything else falls back to a JSON textarea so an exotic shape is
 * still editable (operator types the JSON; we POST verbatim). The
 * card is small on purpose: the registry's contract is "scope =
 * scalar workspace setting"; complex multi-row state (inbox routing
 * rules, FSM stages) needs a richer renderer that lives next door.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

interface ScopeSchema {
  type?: string | string[];
  enum?: unknown[];
  description?: string;
  properties?: Record<string, ScopeSchema>;
}

interface ScopeDetail {
  slug: string;
  description: string;
  json_schema: ScopeSchema;
  current_value: unknown;
}

interface PutResult {
  slug: string;
  value: unknown;
  audit_event: string;
}

type Stage = "loading" | "idle" | "saving" | "saved" | "error";

export function ConfigScopeCard({
  workspaceId,
  scope,
}: {
  workspaceId: string;
  scope: string;
}) {
  const [detail, setDetail] = useState<ScopeDetail | null>(null);
  const [value, setValue] = useState<unknown>(undefined);
  const [stage, setStage] = useState<Stage>("loading");
  const [errMsg, setErrMsg] = useState<string | null>(null);

  // Initial fetch — re-runs on scope/workspace change so the page can
  // keep the card mounted while the user switches workspaces.
  useEffect(() => {
    let cancelled = false;
    setStage("loading");
    setErrMsg(null);
    fetch(
      `/api/proxy/v1/workspaces/${encodeURIComponent(workspaceId)}/config/${encodeURIComponent(scope)}`,
      { method: "GET", cache: "no-store" },
    )
      .then(async (res) => {
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { error?: string; message?: string };
          throw new Error(body.message || body.error || `HTTP ${res.status}`);
        }
        return (await res.json()) as ScopeDetail;
      })
      .then((data) => {
        if (cancelled) return;
        setDetail(data);
        setValue(data.current_value);
        setStage("idle");
      })
      .catch((err) => {
        if (cancelled) return;
        setErrMsg(err instanceof Error ? err.message : String(err));
        setStage("error");
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, scope]);

  const save = useCallback(async () => {
    if (!detail) return;
    setStage("saving");
    setErrMsg(null);
    try {
      const res = await fetch(
        `/api/proxy/v1/workspaces/${encodeURIComponent(workspaceId)}/config/${encodeURIComponent(scope)}`,
        {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value }),
        },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { error?: string; message?: string };
        throw new Error(body.message || body.error || `HTTP ${res.status}`);
      }
      const result = (await res.json()) as PutResult;
      // Backend re-reads after write — keep the form synced with
      // whatever ended up persisted (server may normalise).
      setValue(result.value);
      setDetail({ ...detail, current_value: result.value });
      setStage("saved");
      setTimeout(() => {
        setStage((s) => (s === "saved" ? "idle" : s));
      }, 1500);
    } catch (err) {
      setErrMsg(err instanceof Error ? err.message : String(err));
      setStage("error");
    }
  }, [detail, scope, value, workspaceId]);

  if (stage === "loading") {
    return (
      <div className="rounded-xl border border-white/5 bg-white/[0.02] p-4 text-xs text-white/40">
        Loading <code className="font-mono">{scope}</code>…
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="rounded-xl border border-coral/30 bg-coral/[0.04] p-4 text-xs text-coral/85">
        Couldn&apos;t load <code className="font-mono">{scope}</code>: {errMsg ?? "unknown error"}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.02] p-4">
      <header className="mb-3">
        <h3 className="text-sm font-semibold text-white">
          <code className="font-mono text-aqua/90">{detail.slug}</code>
        </h3>
        <p className="mt-1 text-[12px] text-white/55">{detail.description}</p>
      </header>
      <SchemaInput
        schema={detail.json_schema}
        value={value}
        onChange={setValue}
      />
      <footer className="mt-3 flex items-center justify-between gap-3">
        <span className="text-[11px] text-white/35">
          {stage === "saved"
            ? "Saved."
            : stage === "saving"
              ? "Saving…"
              : stage === "error"
                ? <span className="text-coral/90">{errMsg}</span>
                : null}
        </span>
        <button
          type="button"
          onClick={save}
          disabled={stage === "saving" || _valuesEqual(value, detail.current_value)}
          className="rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-semibold text-aqua transition hover:bg-aqua/15 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Save
        </button>
      </footer>
    </div>
  );
}


function SchemaInput({
  schema,
  value,
  onChange,
}: {
  schema: ScopeSchema;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const types = useMemo(
    () => (Array.isArray(schema.type) ? schema.type : schema.type ? [schema.type] : []),
    [schema.type],
  );

  // String with enum → <select>
  if (types.includes("string") && Array.isArray(schema.enum)) {
    return (
      <select
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-2 text-sm text-white outline-none focus:border-aqua/40"
      >
        {(schema.enum as string[]).map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  }

  // String (nullable or not) without enum → text input
  if (types.includes("string")) {
    const nullable = types.includes("null");
    const current = typeof value === "string" ? value : "";
    return (
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={current}
          onChange={(e) => onChange(e.target.value || (nullable ? null : ""))}
          className="flex-1 rounded border border-white/10 bg-white/[0.04] px-2 py-2 text-sm text-white outline-none focus:border-aqua/40"
        />
        {nullable ? (
          <button
            type="button"
            onClick={() => onChange(null)}
            disabled={value === null}
            className="text-[11px] text-white/40 hover:text-white disabled:opacity-30"
          >
            clear
          </button>
        ) : null}
      </div>
    );
  }

  // Object of bool flags → checkboxes
  if (types.includes("object") && schema.properties) {
    const props = schema.properties;
    const obj = (value && typeof value === "object" ? (value as Record<string, unknown>) : {});
    return (
      <ul className="space-y-2">
        {Object.entries(props).map(([key, subSchema]) => {
          const isBool =
            subSchema.type === "boolean" ||
            (Array.isArray(subSchema.type) && subSchema.type.includes("boolean"));
          if (!isBool) return null;
          const checked = Boolean(obj[key]);
          return (
            <li key={key} className="flex items-center gap-2">
              <input
                type="checkbox"
                id={`scope-prop-${key}`}
                checked={checked}
                onChange={(e) => onChange({ ...obj, [key]: e.target.checked })}
                className="h-4 w-4 cursor-pointer accent-aqua"
              />
              <label
                htmlFor={`scope-prop-${key}`}
                className="text-sm text-white/85"
              >
                <code className="font-mono">{key}</code>
                {subSchema.description ? (
                  <span className="ml-2 text-[11px] text-white/40">
                    {subSchema.description}
                  </span>
                ) : null}
              </label>
            </li>
          );
        })}
      </ul>
    );
  }

  // Fallback: JSON textarea so an exotic shape stays editable.
  return (
    <textarea
      value={JSON.stringify(value, null, 2)}
      onChange={(e) => {
        try {
          onChange(JSON.parse(e.target.value));
        } catch {
          // Don't choke on transient invalid JSON while the user is
          // typing; ``Save`` will surface the server's parse error
          // if it stays broken.
        }
      }}
      rows={6}
      className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-2 font-mono text-xs text-white outline-none focus:border-aqua/40"
    />
  );
}


function _valuesEqual(a: unknown, b: unknown): boolean {
  // Cheap deep equal — sufficient for the shapes the registry
  // emits (scalars + small flat objects). A full deep-equal would
  // pull in a dep without paying back.
  return JSON.stringify(a) === JSON.stringify(b);
}
