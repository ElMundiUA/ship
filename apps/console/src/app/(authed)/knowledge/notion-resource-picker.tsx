"use client";

/**
 * NotionResourcePicker — multi-select picker over the connected Notion
 * integration's visible pages. Replaces the JSON textarea operators
 * used to fill ``resource_refs`` by hand.
 *
 * Searches via ``/api/knowledge/notion-resources`` (Next route handler
 * proxying ``/v1/.../notion/resources``). The picker's ``value`` is
 * the same shape the backend already accepts in
 * ``config.resource_refs``: ``{ page_id: <uuid> }[]`` — so no
 * transform is needed on submit.
 *
 * Defaults to ``type=page`` because the connector fetcher only
 * supports page refs today; database support arrives when the
 * connector learns it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ApiNotionResourceItem } from "@/lib/api/client";

export type NotionPageRef = { page_id: string } | { database_id: string };

type FetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; items: ApiNotionResourceItem[]; nextCursor: string | null; hasMore: boolean }
  | { kind: "error"; message: string };

const SEARCH_DEBOUNCE_MS = 300;

export function NotionResourcePicker({
  workspaceId,
  integrationId,
  value,
  onChange,
}: {
  workspaceId: string;
  integrationId: string;
  value: NotionPageRef[];
  onChange: (next: NotionPageRef[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [state, setState] = useState<FetchState>({ kind: "idle" });
  const [appending, setAppending] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Track selected refs by id so checkmarks stay correct across paginated pages.
  const selectedIds = useMemo(() => new Set(value.map(refId)), [value]);
  // Map id → item for resolving "selected" pills when the item isn't in the
  // current visible list (e.g. user paginated past it).
  const resolvedById = useMemo(() => {
    const map = new Map<string, ApiNotionResourceItem>();
    if (state.kind === "ready") {
      for (const item of state.items) map.set(item.id, item);
    }
    return map;
  }, [state]);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQuery(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [query]);

  const load = useCallback(
    async (
      args: { q: string; cursor?: string | null; append?: boolean },
    ) => {
      if (!integrationId) {
        setState({ kind: "error", message: "Connect Notion first." });
        return;
      }
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      if (args.append) setAppending(true);
      else setState({ kind: "loading" });

      try {
        const params = new URLSearchParams({
          workspaceId,
          integrationId,
          // The connector now supports both pages and databases — pages
          // ingest as a single doc, databases ingest each entry as one
          // doc (capped per source). Default to "any" so operators see
          // both shapes in the picker.
          type: "any",
        });
        if (args.q) params.set("q", args.q);
        if (args.cursor) params.set("cursor", args.cursor);
        const resp = await fetch(`/api/knowledge/notion-resources?${params.toString()}`, {
          method: "GET",
          signal: controller.signal,
        });
        if (!resp.ok) {
          const payload = await resp.json().catch(() => ({}));
          const message =
            typeof payload?.error === "string" ? payload.error : `HTTP ${resp.status}`;
          setState({ kind: "error", message });
          return;
        }
        const data = (await resp.json()) as {
          items: ApiNotionResourceItem[];
          next_cursor: string | null;
          has_more: boolean;
        };
        setState((prev) => {
          const baseItems =
            args.append && prev.kind === "ready" ? prev.items : [];
          const merged = [...baseItems, ...data.items];
          return {
            kind: "ready",
            items: merged,
            nextCursor: data.next_cursor,
            hasMore: data.has_more,
          };
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to load Notion resources",
        });
      } finally {
        setAppending(false);
      }
    },
    [integrationId, workspaceId],
  );

  // Re-run search whenever debounced query or integration changes.
  useEffect(() => {
    if (!integrationId) return;
    void load({ q: debouncedQuery });
  }, [debouncedQuery, integrationId, load]);

  function toggle(item: ApiNotionResourceItem) {
    const ref: NotionPageRef =
      item.type === "page"
        ? { page_id: item.id }
        : { database_id: item.id };
    const id = item.id;
    if (selectedIds.has(id)) {
      onChange(value.filter((existing) => refId(existing) !== id));
    } else {
      onChange([...value, ref]);
    }
  }

  function unselect(id: string) {
    onChange(value.filter((existing) => refId(existing) !== id));
  }

  return (
    <div className="space-y-3" data-testid="notion-resource-picker">
      <input
        type="text"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search Notion pages and databases your integration can see…"
        className="input-ship input-ship-wizard"
        aria-label="Search Notion pages and databases"
      />

      {value.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {value.map((ref) => {
            const id = refId(ref);
            const item = resolvedById.get(id);
            const label = item?.title ?? truncateId(id);
            const icon = item?.icon ?? null;
            return (
              <span
                key={id}
                className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/10 px-2 py-0.5 text-[11px] text-aqua"
              >
                {icon && <span className="text-xs">{maybeRenderIcon(icon)}</span>}
                <span className="max-w-[24ch] truncate">{label}</span>
                <button
                  type="button"
                  onClick={() => unselect(id)}
                  className="text-aqua/70 hover:text-aqua"
                  aria-label={`Remove ${label}`}
                >
                  ×
                </button>
              </span>
            );
          })}
        </div>
      )}

      <div className="rounded border border-white/10 bg-black/30">
        {state.kind === "loading" && (
          <p className="px-3 py-3 text-xs text-white/55">Loading…</p>
        )}
        {state.kind === "error" && (
          <p className="px-3 py-3 text-xs text-coral">{state.message}</p>
        )}
        {state.kind === "ready" && state.items.length === 0 && (
          <p className="px-3 py-3 text-xs text-white/55">
            Nothing matches. Make sure your integration is shared with the page or database in Notion (Share → Connections).
          </p>
        )}
        {state.kind === "ready" && state.items.length > 0 && (
          <ul className="max-h-72 divide-y divide-white/5 overflow-y-auto" role="listbox">
            {state.items.map((item) => {
              const checked = selectedIds.has(item.id);
              return (
                <li key={item.id}>
                  <label className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs text-white/85 hover:bg-white/[0.04]">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(item)}
                      className="accent-aqua"
                      aria-label={item.title}
                    />
                    {item.icon ? (
                      <span className="w-4 text-center">{maybeRenderIcon(item.icon)}</span>
                    ) : (
                      <span className="w-4 text-center text-white/35">{item.type === "database" ? "▦" : "•"}</span>
                    )}
                    <span className="flex-1 truncate">
                      <span className="text-white/90">{item.title || "Untitled"}</span>
                      <span className="ml-2 text-white/35">{item.type === "database" ? "database" : "page"}</span>
                    </span>
                    {item.last_edited_time && (
                      <span className="text-[10px] text-white/35">
                        {formatDate(item.last_edited_time)}
                      </span>
                    )}
                  </label>
                </li>
              );
            })}
          </ul>
        )}
        {state.kind === "ready" && state.hasMore && state.nextCursor && (
          <button
            type="button"
            disabled={appending}
            onClick={() => void load({ q: debouncedQuery, cursor: state.nextCursor, append: true })}
            className="w-full border-t border-white/5 px-3 py-2 text-[11px] text-aqua/85 hover:text-aqua disabled:opacity-40"
          >
            {appending ? "Loading more…" : "Load more"}
          </button>
        )}
      </div>

      <p className="text-[11px] text-white/45">
        Pick a page (one doc) or a database (each entry becomes one doc, capped at 50). Don&apos;t see what you need? In Notion, open the page → <em>•••</em> → Connections and add the integration.
      </p>
    </div>
  );
}

function refId(ref: NotionPageRef): string {
  return "page_id" in ref ? ref.page_id : ref.database_id;
}

function maybeRenderIcon(icon: string): string {
  // Notion icons can be emoji strings or external image URLs. Show the
  // emoji directly; for URLs we'd need <img> with sized rendering, which
  // adds visual complexity for marginal value — skip and fall back to
  // the type glyph by returning empty.
  if (!icon) return "";
  if (icon.length <= 4 && !icon.startsWith("http")) return icon;
  return "";
}

function truncateId(id: string): string {
  return id.length > 18 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}
