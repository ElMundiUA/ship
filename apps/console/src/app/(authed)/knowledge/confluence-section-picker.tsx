"use client";

/**
 * ConfluenceSectionPicker — pick a space, then check sections (= top-level
 * page subtrees) to ingest. Each checked section emits one resource_ref:
 *
 *     { root_page_id, space_id, space_key, space_name, title }
 *
 * The connector (`backend/app/services/connectors/confluence.py`)
 * resolves that ref into root + every descendant at sync time, so one
 * "section" pick maps to a whole chapter/handbook in the source.
 *
 * Spaces themselves aren't directly selectable — operators always pick at
 * least one section. This avoids accidentally ingesting an entire
 * thousand-page space and getting truncated by the per-source document
 * cap; the section unit is the right granularity.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  ApiConfluenceSection,
  ApiConfluenceSpace,
} from "@/lib/api/client";

export type ConfluenceSectionRef = {
  root_page_id: string;
  space_id: string;
  space_key: string | null;
  space_name: string | null;
  title: string;
};

type SpaceState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; spaces: ApiConfluenceSpace[] }
  | { kind: "error"; message: string };

type SectionState =
  | { kind: "idle" }
  | { kind: "loading" }
  | {
      kind: "ready";
      sections: ApiConfluenceSection[];
      nextCursor: string | null;
      hasMore: boolean;
    }
  | { kind: "error"; message: string };

export function ConfluenceSectionPicker({
  workspaceId,
  integrationId,
  value,
  onChange,
}: {
  workspaceId: string;
  integrationId: string;
  value: ConfluenceSectionRef[];
  onChange: (next: ConfluenceSectionRef[]) => void;
}) {
  const [spaceState, setSpaceState] = useState<SpaceState>({ kind: "idle" });
  const [sectionState, setSectionState] = useState<SectionState>({ kind: "idle" });
  const [appending, setAppending] = useState(false);
  const [activeSpaceId, setActiveSpaceId] = useState<string>("");
  const sectionAbortRef = useRef<AbortController | null>(null);

  const selectedIds = useMemo(
    () => new Set(value.map((ref) => ref.root_page_id)),
    [value],
  );

  // Resolve already-picked refs back to display info so pills survive a
  // space switch (the active section list won't contain them).
  const resolvedById = useMemo(() => {
    const map = new Map<string, ConfluenceSectionRef>();
    for (const ref of value) map.set(ref.root_page_id, ref);
    return map;
  }, [value]);

  // Load spaces once per (workspace, integration).
  useEffect(() => {
    if (!integrationId) {
      setSpaceState({ kind: "error", message: "Connect Confluence first." });
      return;
    }
    let cancelled = false;
    setSpaceState({ kind: "loading" });
    (async () => {
      try {
        const params = new URLSearchParams({ workspaceId, integrationId });
        const resp = await fetch(`/api/knowledge/confluence-spaces?${params.toString()}`, {
          method: "GET",
        });
        if (cancelled) return;
        if (!resp.ok) {
          const payload = await resp.json().catch(() => ({}));
          const message =
            typeof payload?.error === "string" ? payload.error : `HTTP ${resp.status}`;
          setSpaceState({ kind: "error", message });
          return;
        }
        const data = (await resp.json()) as { items: ApiConfluenceSpace[] };
        setSpaceState({ kind: "ready", spaces: data.items });
        if (data.items.length > 0 && !activeSpaceId) {
          setActiveSpaceId(data.items[0].id);
        }
      } catch (err) {
        if (cancelled) return;
        setSpaceState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to load spaces",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeSpaceId, integrationId, workspaceId]);

  const loadSections = useCallback(
    async (args: { spaceId: string; cursor?: string | null; append?: boolean }) => {
      if (!args.spaceId) return;
      sectionAbortRef.current?.abort();
      const controller = new AbortController();
      sectionAbortRef.current = controller;

      if (args.append) setAppending(true);
      else setSectionState({ kind: "loading" });

      try {
        const params = new URLSearchParams({
          workspaceId,
          integrationId,
          spaceId: args.spaceId,
        });
        if (args.cursor) params.set("cursor", args.cursor);
        const resp = await fetch(
          `/api/knowledge/confluence-sections?${params.toString()}`,
          { method: "GET", signal: controller.signal },
        );
        if (!resp.ok) {
          const payload = await resp.json().catch(() => ({}));
          const message =
            typeof payload?.error === "string" ? payload.error : `HTTP ${resp.status}`;
          setSectionState({ kind: "error", message });
          return;
        }
        const data = (await resp.json()) as {
          items: ApiConfluenceSection[];
          next_cursor: string | null;
          has_more: boolean;
        };
        setSectionState((prev) => {
          const baseSections =
            args.append && prev.kind === "ready" ? prev.sections : [];
          return {
            kind: "ready",
            sections: [...baseSections, ...data.items],
            nextCursor: data.next_cursor,
            hasMore: data.has_more,
          };
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        setSectionState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to load sections",
        });
      } finally {
        setAppending(false);
      }
    },
    [integrationId, workspaceId],
  );

  // Reload sections whenever the active space changes.
  useEffect(() => {
    if (activeSpaceId) {
      void loadSections({ spaceId: activeSpaceId });
    }
  }, [activeSpaceId, loadSections]);

  function toggle(section: ApiConfluenceSection) {
    const id = section.id;
    if (selectedIds.has(id)) {
      onChange(value.filter((existing) => existing.root_page_id !== id));
    } else {
      onChange([
        ...value,
        {
          root_page_id: id,
          space_id: section.space_id,
          space_key: section.space_key,
          space_name: section.space_name,
          title: section.title,
        },
      ]);
    }
  }

  function unselect(id: string) {
    onChange(value.filter((existing) => existing.root_page_id !== id));
  }

  return (
    <div className="space-y-3" data-testid="confluence-section-picker">
      {/* Space chooser */}
      {spaceState.kind === "loading" && (
        <p className="text-xs text-white/55">Loading spaces…</p>
      )}
      {spaceState.kind === "error" && (
        <p className="text-xs text-coral">{spaceState.message}</p>
      )}
      {spaceState.kind === "ready" && spaceState.spaces.length === 0 && (
        <p className="text-xs text-white/55">
          No Confluence spaces visible. Make sure your API token user has access to at
          least one space.
        </p>
      )}
      {spaceState.kind === "ready" && spaceState.spaces.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {spaceState.spaces.map((space) => {
            const active = space.id === activeSpaceId;
            return (
              <button
                key={space.id}
                type="button"
                onClick={() => setActiveSpaceId(space.id)}
                className={`rounded-full border px-3 py-1 text-[11px] transition ${
                  active
                    ? "border-aqua/60 bg-aqua/15 text-aqua"
                    : "border-white/10 bg-white/[0.03] text-white/60 hover:border-white/30"
                }`}
              >
                <span className="font-semibold">{space.name}</span>
                <span className="ml-1.5 text-white/40">{space.key}</span>
              </button>
            );
          })}
        </div>
      )}

      {/* Selected pills */}
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-white/5 pt-3">
          {value.map((ref) => {
            const resolved = resolvedById.get(ref.root_page_id) ?? ref;
            return (
              <span
                key={ref.root_page_id}
                className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/10 px-2 py-0.5 text-[11px] text-aqua"
              >
                <span className="max-w-[28ch] truncate">
                  {resolved.title}
                  {resolved.space_key && (
                    <span className="ml-1 text-aqua/55">· {resolved.space_key}</span>
                  )}
                </span>
                <button
                  type="button"
                  onClick={() => unselect(ref.root_page_id)}
                  className="text-aqua/70 hover:text-aqua"
                  aria-label={`Remove ${resolved.title}`}
                >
                  ×
                </button>
              </span>
            );
          })}
        </div>
      )}

      {/* Section list */}
      <div className="rounded border border-white/10 bg-black/30">
        {sectionState.kind === "loading" && (
          <p className="px-3 py-3 text-xs text-white/55">Loading sections…</p>
        )}
        {sectionState.kind === "error" && (
          <p className="px-3 py-3 text-xs text-coral">{sectionState.message}</p>
        )}
        {sectionState.kind === "ready" && sectionState.sections.length === 0 && (
          <p className="px-3 py-3 text-xs text-white/55">
            No top-level pages in this space — nothing to ingest as a section.
          </p>
        )}
        {sectionState.kind === "ready" && sectionState.sections.length > 0 && (
          <ul className="max-h-72 divide-y divide-white/5 overflow-y-auto" role="listbox">
            {sectionState.sections.map((section) => {
              const checked = selectedIds.has(section.id);
              return (
                <li key={section.id}>
                  <label className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs text-white/85 hover:bg-white/[0.04]">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(section)}
                      className="accent-aqua"
                      aria-label={section.title}
                    />
                    <span className="flex-1 truncate">
                      <span className="text-white/90">{section.title || "Untitled"}</span>
                    </span>
                    {section.last_edited_time && (
                      <span className="text-[10px] text-white/35">
                        {formatDate(section.last_edited_time)}
                      </span>
                    )}
                  </label>
                </li>
              );
            })}
          </ul>
        )}
        {sectionState.kind === "ready" && sectionState.hasMore && sectionState.nextCursor && (
          <button
            type="button"
            disabled={appending}
            onClick={() =>
              void loadSections({
                spaceId: activeSpaceId,
                cursor: sectionState.nextCursor,
                append: true,
              })
            }
            className="w-full border-t border-white/5 px-3 py-2 text-[11px] text-aqua/85 hover:text-aqua disabled:opacity-40"
          >
            {appending ? "Loading more…" : "Load more"}
          </button>
        )}
      </div>

      <p className="text-[11px] text-white/45">
        Each section ingests its root page plus every descendant. Pick the chapters you
        want; pick from multiple spaces by switching the space chip above.
      </p>
    </div>
  );
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
