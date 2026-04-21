"use client";

/**
 * Sidebar listing the workspace's knowledge buckets.
 *
 * Buckets are the "named memory" of the agent: every time the user
 * starts a fresh thread (or accepts a topic-shift banner), the
 * outgoing thread is packed into a bucket and a new summary row is
 * appended. The sidebar lets the user browse those buckets, read
 * their summaries, rename or archive them, and (later) resume a
 * conversation anchored to an existing bucket.
 *
 * This is a "pragmatic" UI — it trades polish for visibility: the
 * user can see what the agent thinks it's been doing, which is
 * often enough of a trust-building nudge to get people to trust
 * the agent with real planning work.
 */

import { useCallback, useEffect, useState } from "react";

type Bucket = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  summary_count: number;
  // Phase 4b scope fields (optional — tolerate older backends).
  scope_kind?: "workspace" | "project" | "repo" | "user";
  source_kind?:
    | "agent_memory"
    | "repo_files"
    | "external_static"
    | "connector_proxy"
    | "audio_transcript";
  repo_id?: string | null;
  project_id?: string | null;
  user_id?: string | null;
};

export type BucketScopeFilter =
  | { kind: "workspace" }
  | { kind: "repo"; repoId: string | null }
  | { kind: "user"; userId: string | null };

type Summary = {
  id: string;
  bucket_id: string;
  thread_id: string | null;
  title: string;
  summary: string;
  created_at: string;
};

export function BucketsSidebar({
  workspaceId,
  initial,
  scopeFilter,
}: {
  workspaceId: string;
  initial: Bucket[];
  /**
   * Phase 4b: optional scope pre-filter driven by the AppShell
   * scope pill. Repo scope keeps buckets visible to that repo
   * (repo-scoped rows + ambient workspace-scope rows — the same
   * inheritance model the Phase 3 resolver uses). User scope keeps
   * the caller's own agent-memory + workspace ambient rows;
   * someone else's ``scope='user'`` rows are invisible either way
   * (backend filters them for us). Workspace scope is the
   * identity filter.
   */
  scopeFilter?: BucketScopeFilter;
}) {
  const [buckets, setBuckets] = useState<Bucket[]>(initial);
  const [selected, setSelected] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<Summary[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);

  const refreshBuckets = useCallback(async () => {
    try {
      const qs = showArchived ? "?include_archived=true" : "";
      const res = await fetch(
        `/api/chat/buckets?workspace_id=${encodeURIComponent(workspaceId)}${qs ? "&include_archived=1" : ""}`,
      );
      if (!res.ok) {
        const t = await res.text().catch(() => "");
        throw new Error(t || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as Bucket[];
      setBuckets(data);
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : String(err));
    }
  }, [workspaceId, showArchived]);

  // Refresh whenever the "include archived" toggle flips. On first
  // mount we already have the SSR payload, so no call there.
  const firstRender = useFirstRender();
  useEffect(() => {
    if (firstRender) return;
    void refreshBuckets();
  }, [firstRender, refreshBuckets]);

  const select = useCallback(
    async (slug: string) => {
      setSelected(slug);
      setLoading(true);
      setErrorText(null);
      try {
        const res = await fetch(
          `/api/chat/buckets/${encodeURIComponent(slug)}/summaries?workspace_id=${encodeURIComponent(workspaceId)}`,
        );
        if (!res.ok) {
          const t = await res.text().catch(() => "");
          throw new Error(t || `HTTP ${res.status}`);
        }
        setSummaries((await res.json()) as Summary[]);
      } catch (err) {
        setErrorText(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [workspaceId],
  );

  const rename = useCallback(
    async (slug: string, name: string) => {
      const res = await fetch(
        `/api/chat/buckets/${encodeURIComponent(slug)}?workspace_id=${encodeURIComponent(workspaceId)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name }),
        },
      );
      if (res.ok) {
        const updated = (await res.json()) as Bucket;
        setBuckets((prev) =>
          prev.map((b) => (b.slug === slug ? updated : b)),
        );
      }
    },
    [workspaceId],
  );

  const toggleArchive = useCallback(
    async (slug: string, archived: boolean) => {
      const res = await fetch(
        `/api/chat/buckets/${encodeURIComponent(slug)}?workspace_id=${encodeURIComponent(workspaceId)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ archived }),
        },
      );
      if (res.ok) {
        await refreshBuckets();
      }
    },
    [workspaceId, refreshBuckets],
  );

  // First archival filter, then scope filter. The scope filter is
  // tolerant: if a bucket doesn't carry ``scope_kind`` yet (older
  // backend / Phase-0 row that never went through Phase 1), treat
  // it as workspace-scope so the UI doesn't lose sight of it.
  const visible = buckets
    .filter((b) => (showArchived ? true : b.archived_at === null))
    .filter((b) => matchesScope(b, scopeFilter));

  return (
    <aside className="flex h-[calc(100vh-16rem)] min-h-[32rem] flex-col rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div className="flex items-center gap-2 border-b border-white/5 pb-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-white/60">
          Memory buckets
        </h3>
        <label className="ml-auto flex items-center gap-1 text-[10px] text-white/45">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />
          archived
        </label>
      </div>

      {visible.length === 0 ? (
        <p className="mt-3 text-[11px] text-white/45">
          No buckets yet. They show up here once you pack a conversation
          (or when the agent auto-packs on a topic shift).
        </p>
      ) : (
        <ul className="mt-2 flex-1 space-y-1 overflow-y-auto">
          {visible.map((b) => (
            <li key={b.id}>
              <button
                type="button"
                onClick={() => select(b.slug)}
                className={`w-full rounded-md border px-2 py-1.5 text-left transition ${
                  selected === b.slug
                    ? "border-aqua/40 bg-aqua/10"
                    : "border-transparent hover:border-white/10 hover:bg-white/[0.03]"
                }`}
              >
                <div className="flex items-center gap-2 text-[12px] text-white/90">
                  <span className="truncate font-semibold">{b.name}</span>
                  <span className="ml-auto text-[10px] text-white/40">
                    {b.summary_count}
                  </span>
                </div>
                {b.description ? (
                  <p className="mt-0.5 line-clamp-2 text-[10px] text-white/45">
                    {b.description}
                  </p>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected ? (
        <BucketDetail
          slug={selected}
          bucket={buckets.find((b) => b.slug === selected)}
          summaries={summaries}
          loading={loading}
          error={errorText}
          onRename={rename}
          onArchive={(slug) => toggleArchive(slug, true)}
          onUnarchive={(slug) => toggleArchive(slug, false)}
          onClose={() => {
            setSelected(null);
            setSummaries([]);
          }}
        />
      ) : null}
    </aside>
  );
}

function matchesScope(
  b: Bucket,
  filter: BucketScopeFilter | undefined,
): boolean {
  if (!filter || filter.kind === "workspace") return true;
  const kind = b.scope_kind ?? "workspace";
  if (filter.kind === "repo") {
    if (kind === "workspace") return true;
    if (kind === "repo" && filter.repoId && b.repo_id === filter.repoId) {
      return true;
    }
    return false;
  }
  // ``user`` scope: caller's agent memory + ambient workspace.
  if (kind === "workspace") return true;
  if (kind === "user") {
    // Backend already filters other users out; accept any user row
    // that comes back, which belongs to the caller by construction.
    return true;
  }
  return false;
}

function BucketDetail({
  slug,
  bucket,
  summaries,
  loading,
  error,
  onRename,
  onArchive,
  onUnarchive,
  onClose,
}: {
  slug: string;
  bucket: Bucket | undefined;
  summaries: Summary[];
  loading: boolean;
  error: string | null;
  onRename: (slug: string, name: string) => Promise<void>;
  onArchive: (slug: string) => Promise<void>;
  onUnarchive: (slug: string) => Promise<void>;
  onClose: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(bucket?.name ?? "");

  useEffect(() => {
    setDraftName(bucket?.name ?? "");
    setEditing(false);
  }, [bucket?.name, slug]);

  if (!bucket) return null;

  return (
    <div className="mt-3 border-t border-white/10 pt-3">
      <div className="flex items-center gap-2">
        {editing ? (
          <input
            autoFocus
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onBlur={async () => {
              if (draftName.trim() && draftName !== bucket.name) {
                await onRename(slug, draftName.trim());
              }
              setEditing(false);
            }}
            className="flex-1 rounded-md border border-white/20 bg-black/40 px-2 py-1 text-[12px] text-white"
          />
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="truncate text-[12px] font-semibold text-white hover:underline"
            title="Rename"
          >
            {bucket.name}
          </button>
        )}
        <button
          type="button"
          onClick={onClose}
          className="ml-auto text-[11px] text-white/45 hover:text-white"
          aria-label="close"
        >
          ✕
        </button>
      </div>

      <div className="mt-1 flex items-center gap-2 text-[10px] text-white/40">
        <code>{bucket.slug}</code>
        <span>·</span>
        <span>{bucket.summary_count} summaries</span>
        <button
          type="button"
          onClick={() =>
            bucket.archived_at ? onUnarchive(slug) : onArchive(slug)
          }
          className="ml-auto rounded-md border border-white/10 px-2 py-0.5 hover:border-white/30 hover:text-white"
        >
          {bucket.archived_at ? "unarchive" : "archive"}
        </button>
      </div>

      {error ? (
        <p className="mt-2 text-[11px] text-rose-300">{error}</p>
      ) : null}
      {loading ? (
        <p className="mt-2 text-[11px] text-white/40">Loading summaries…</p>
      ) : summaries.length === 0 ? (
        <p className="mt-2 text-[11px] text-white/40">
          No summaries packed yet.
        </p>
      ) : (
        <ul className="mt-2 space-y-2 text-[11px] text-white/75">
          {summaries.map((s) => (
            <li
              key={s.id}
              className="rounded-md border border-white/5 bg-black/20 p-2"
            >
              <div className="text-white/90">{s.title}</div>
              <p className="mt-0.5 line-clamp-4 text-white/55">{s.summary}</p>
              <div className="mt-1 text-[10px] text-white/30">
                {new Date(s.created_at).toLocaleString()}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function useFirstRender(): boolean {
  const [first, setFirst] = useState(true);
  useEffect(() => {
    setFirst(false);
  }, []);
  return first;
}
