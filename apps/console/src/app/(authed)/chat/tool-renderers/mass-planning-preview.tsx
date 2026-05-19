"use client";

/**
 * ELS-172, ELS-173 (M5+M6) — preview + edit card for a mass-planning
 * proposal emitted by Navigator's ``propose_mass_plan`` tool.
 *
 * Mounted inline in the chat tool-renderer registry. Reads the
 * proposal via ``GET /v1/.../planning/proposals/{id}``, lets the
 * operator rename / split / merge / re-link deps / delete epics,
 * then commits the draft to Linear via
 * ``POST /v1/.../planning/mass-import``.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui";
import { proxyApiFetch } from "@/lib/api/proxy-client";
import { cn } from "@/lib/cn";

// ---------------------------------------------------------------------------
// Schema (mirrors backend MassPlanProposal + PlanningProposalOut)
// ---------------------------------------------------------------------------

type EpicProposal = {
  key: string;
  title: string;
  brief: string;
  depends_on: string[];
};

type Payload = {
  project: { name: string; description: string };
  epics: EpicProposal[];
};

type PlanningProposalOut = {
  id: string;
  workspace_id: string;
  payload: Payload & Record<string, unknown>;
  committed_at: string | null;
  committed_ticket_refs: string[] | null;
};

type ToolResult = {
  proposal_id?: string;
  project_name?: string;
  epic_count?: number;
  dep_count?: number;
  summary?: string;
};

type CommitResponse = {
  proposal_id: string;
  project_id: string;
  project_url: string | null;
  anchors: { key: string; ticket_ref: string; url: string | null }[];
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseToolResult(raw: unknown): ToolResult | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.proposal_id !== "string") return null;
  return {
    proposal_id: r.proposal_id,
    project_name: typeof r.project_name === "string" ? r.project_name : undefined,
    epic_count: typeof r.epic_count === "number" ? r.epic_count : undefined,
    dep_count: typeof r.dep_count === "number" ? r.dep_count : undefined,
    summary: typeof r.summary === "string" ? r.summary : undefined,
  };
}

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function RenderProposeMassPlan(raw: unknown) {
  const parsed = parseToolResult(raw);
  if (!parsed?.proposal_id) {
    return <div className="text-xs italic text-white/40">No proposal_id in tool result.</div>;
  }
  return <MassPlanningPreviewCard proposalId={parsed.proposal_id} headline={parsed.summary} />;
}

function MassPlanningPreviewCard({
  proposalId,
  headline,
}: {
  proposalId: string;
  headline?: string;
}) {
  const [proposal, setProposal] = useState<PlanningProposalOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [committed, setCommitted] = useState<CommitResponse | null>(null);

  const workspaceId = proposal?.workspace_id;

  // Local editable copy of payload — applied to server via PATCH.
  const [draft, setDraft] = useState<Payload | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await proxyApiFetch<PlanningProposalOut>(
        `/v1/workspaces/${proposal?.workspace_id ?? ""}/planning/proposals/${proposalId}`,
        { method: "GET" },
      );
      setProposal(data);
      setDraft(data.payload as Payload);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load proposal");
    } finally {
      setLoading(false);
    }
  }, [proposalId, proposal?.workspace_id]);

  // Initial load — we don't know workspace_id until first fetch, so fall back
  // to a relative call that resolves at the API layer via session context.
  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);
      try {
        // First-time fetch: server returns it via session-scoped workspace.
        // The /planning/proposals/{id} endpoint requires workspace_id in
        // path. We pull it from the inbox API's me-context header which
        // every authed page already has. As a pragmatic fallback, look it
        // up via /v1/workspaces and pick the active one.
        const sess = await proxyApiFetch<{ workspaces: { id: string }[] }>(
          `/v1/workspaces`,
          { method: "GET" },
        ).catch(() => null);
        const wsId = sess?.workspaces?.[0]?.id;
        if (!wsId) {
          throw new Error("No workspace in session");
        }
        const data = await proxyApiFetch<PlanningProposalOut>(
          `/v1/workspaces/${wsId}/planning/proposals/${proposalId}`,
          { method: "GET" },
        );
        setProposal(data);
        setDraft(data.payload as Payload);
        if (data.committed_at && data.committed_ticket_refs) {
          // already committed previously
          setCommitted({
            proposal_id: data.id,
            project_id: String((data.payload as Record<string, unknown>)._committed_project_id ?? ""),
            project_url: (data.payload as Record<string, unknown>)._committed_project_url as string | null,
            anchors: data.committed_ticket_refs.map((ref, i) => ({
              key: ((data.payload as Record<string, unknown>)._committed_anchor_keys as string[] | undefined)?.[i] ?? `e${i + 1}`,
              ticket_ref: ref,
              url: null,
            })),
          });
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load proposal");
      } finally {
        setLoading(false);
      }
    })();
  }, [proposalId]);

  const savePatch = useCallback(
    async (next: Payload) => {
      if (!workspaceId) return;
      setBusy(true);
      setError(null);
      try {
        const updated = await proxyApiFetch<PlanningProposalOut>(
          `/v1/workspaces/${workspaceId}/planning/proposals/${proposalId}`,
          { method: "PATCH", body: { payload: next } },
        );
        setProposal(updated);
        setDraft(updated.payload as Payload);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to save");
      } finally {
        setBusy(false);
      }
    },
    [proposalId, workspaceId],
  );

  const commit = useCallback(async () => {
    if (!workspaceId) return;
    setBusy(true);
    setError(null);
    try {
      const res = await proxyApiFetch<CommitResponse>(
        `/v1/workspaces/${workspaceId}/planning/mass-import`,
        { method: "POST", body: { proposal_id: proposalId } },
      );
      setCommitted(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to commit");
    } finally {
      setBusy(false);
    }
  }, [proposalId, workspaceId]);

  const discard = useCallback(async () => {
    if (!workspaceId) return;
    if (!confirm("Discard this draft proposal?")) return;
    setBusy(true);
    setError(null);
    try {
      await proxyApiFetch(
        `/v1/workspaces/${workspaceId}/planning/proposals/${proposalId}`,
        { method: "DELETE" },
      );
      setProposal(null);
      setDraft(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to discard");
    } finally {
      setBusy(false);
    }
  }, [proposalId, workspaceId]);

  // Edit ops — operate on the local draft, then call savePatch.
  const renameEpic = (key: string, title: string) => {
    if (!draft) return;
    const next = { ...draft, epics: draft.epics.map((e) => (e.key === key ? { ...e, title } : e)) };
    setDraft(next);
  };
  const editBrief = (key: string, brief: string) => {
    if (!draft) return;
    const next = { ...draft, epics: draft.epics.map((e) => (e.key === key ? { ...e, brief } : e)) };
    setDraft(next);
  };
  const toggleDep = (epicKey: string, depKey: string) => {
    if (!draft) return;
    const next = {
      ...draft,
      epics: draft.epics.map((e) => {
        if (e.key !== epicKey) return e;
        const has = e.depends_on.includes(depKey);
        return { ...e, depends_on: has ? e.depends_on.filter((d) => d !== depKey) : [...e.depends_on, depKey] };
      }),
    };
    setDraft(next);
  };
  const deleteEpic = (key: string) => {
    if (!draft) return;
    const next = {
      ...draft,
      epics: draft.epics.filter((e) => e.key !== key).map((e) => ({ ...e, depends_on: e.depends_on.filter((d) => d !== key) })),
    };
    setDraft(next);
  };
  const splitEpic = (key: string) => {
    if (!draft) return;
    const target = draft.epics.find((e) => e.key === key);
    if (!target) return;
    const idx = draft.epics.findIndex((e) => e.key === key);
    const newKey = `${target.key}-b`;
    const newEpic: EpicProposal = {
      key: newKey,
      title: `${target.title} (2/2)`,
      brief: target.brief,
      depends_on: [target.key], // sequenced after the first half by default
    };
    const next = { ...draft, epics: [...draft.epics.slice(0, idx + 1), newEpic, ...draft.epics.slice(idx + 1)] };
    setDraft(next);
  };

  const applyEdits = async () => {
    if (!draft) return;
    await savePatch(draft);
    setEdit(false);
  };

  if (loading) {
    return <div className="text-xs italic text-white/40">Loading proposal…</div>;
  }
  if (error && !proposal) {
    return <div className="text-xs font-semibold text-coral">Error: {error}</div>;
  }
  if (!proposal || !draft) {
    return <div className="text-xs italic text-white/40">Proposal not available.</div>;
  }

  if (committed) {
    return (
      <div className="rounded-2xl border border-aqua/30 bg-aqua/[0.04] px-4 py-3">
        <div className="text-[10px] font-bold uppercase tracking-wider text-aqua">
          Committed
        </div>
        <div className="mt-1 font-semibold text-white">
          {(proposal.payload as Payload).project.name}
        </div>
        <div className="mt-2 text-xs text-white/70">
          {committed.anchors.length} epic anchors created in Linear.
        </div>
        {committed.project_url && (
          <a
            href={committed.project_url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-2 inline-block text-[11px] font-semibold uppercase tracking-wide text-aqua hover:text-white"
          >
            Open in Linear ↗
          </a>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] font-bold uppercase tracking-wider text-white/40">
            Mass-planning proposal · draft
          </div>
          <div className="mt-1 truncate font-semibold text-white">
            {draft.project.name}
          </div>
          <div className="mt-0.5 text-xs text-white/55 line-clamp-2">
            {draft.project.description}
          </div>
        </div>
        <div className="flex shrink-0 gap-1.5">
          {edit ? (
            <button
              type="button"
              disabled={busy}
              onClick={applyEdits}
              className="rounded-md bg-aqua/15 px-2.5 py-1 text-xs font-semibold text-aqua hover:bg-aqua/25 disabled:opacity-40"
            >
              Save edits
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => setEdit(true)}
              className="rounded-md border border-white/15 px-2.5 py-1 text-xs font-semibold text-white/80 hover:border-aqua hover:text-aqua disabled:opacity-40"
            >
              Edit
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={commit}
            className="rounded-md bg-aqua/15 px-2.5 py-1 text-xs font-semibold text-aqua hover:bg-aqua/25 disabled:opacity-40"
          >
            Commit
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={discard}
            className="rounded-md border border-coral/30 px-2.5 py-1 text-xs font-semibold text-coral/80 hover:bg-coral/10 hover:text-coral disabled:opacity-40"
          >
            Discard
          </button>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-2 text-[11px] text-white/60">
        <Badge tone="neutral">{draft.epics.length} epics</Badge>
        <Badge tone="neutral">
          {draft.epics.reduce((acc, e) => acc + e.depends_on.length, 0)} deps
        </Badge>
        {headline && <span className="text-white/40">· {headline}</span>}
      </div>

      <ul className="mt-3 divide-y divide-white/[0.06]">
        {draft.epics.map((epic) => (
          <EpicRow
            key={epic.key}
            epic={epic}
            allKeys={draft.epics.map((e) => e.key)}
            edit={edit}
            onRename={(t) => renameEpic(epic.key, t)}
            onEditBrief={(b) => editBrief(epic.key, b)}
            onToggleDep={(d) => toggleDep(epic.key, d)}
            onDelete={() => deleteEpic(epic.key)}
            onSplit={() => splitEpic(epic.key)}
          />
        ))}
      </ul>

      {error && (
        <p className="mt-2 text-[11px] font-semibold text-coral">{error}</p>
      )}
    </div>
  );
}

function EpicRow({
  epic,
  allKeys,
  edit,
  onRename,
  onEditBrief,
  onToggleDep,
  onDelete,
  onSplit,
}: {
  epic: EpicProposal;
  allKeys: string[];
  edit: boolean;
  onRename: (t: string) => void;
  onEditBrief: (b: string) => void;
  onToggleDep: (d: string) => void;
  onDelete: () => void;
  onSplit: () => void;
}) {
  const [showBrief, setShowBrief] = useState(false);
  const otherKeys = useMemo(
    () => allKeys.filter((k) => k !== epic.key),
    [allKeys, epic.key],
  );
  return (
    <li className="py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <code className="font-mono text-[10px] text-white/30">{epic.key}</code>
            {edit ? (
              <input
                type="text"
                value={epic.title}
                onChange={(e) => onRename(e.target.value)}
                className="min-w-0 flex-1 rounded-md border border-white/10 bg-white/[0.03] px-2 py-0.5 text-sm text-white focus:border-aqua focus:outline-none"
              />
            ) : (
              <span className="truncate text-sm font-medium text-white">{epic.title}</span>
            )}
          </div>
          {epic.depends_on.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {epic.depends_on.map((d) => (
                <span
                  key={d}
                  className={cn(
                    "rounded-full bg-white/[0.06] px-2 py-0.5 font-mono text-[10px] text-white/55",
                    edit && "cursor-pointer hover:bg-coral/15 hover:text-coral",
                  )}
                  onClick={() => edit && onToggleDep(d)}
                  title={edit ? "Click to remove dep" : `blocked by ${d}`}
                >
                  ← {d}
                </span>
              ))}
            </div>
          )}
          {edit && otherKeys.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-1 text-[10px] text-white/40">
              <span>add dep:</span>
              {otherKeys
                .filter((k) => !epic.depends_on.includes(k))
                .map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => onToggleDep(k)}
                    className="rounded-full border border-white/10 px-1.5 py-0.5 font-mono hover:border-aqua hover:text-aqua"
                  >
                    + {k}
                  </button>
                ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => setShowBrief((s) => !s)}
            className="text-[10px] font-semibold uppercase tracking-wide text-white/40 hover:text-white"
          >
            {showBrief ? "Hide" : "Brief"}
          </button>
          {edit && (
            <>
              <button
                type="button"
                onClick={onSplit}
                className="text-[10px] font-semibold uppercase tracking-wide text-white/40 hover:text-aqua"
                title="Split into two"
              >
                Split
              </button>
              <button
                type="button"
                onClick={onDelete}
                className="text-[10px] font-semibold uppercase tracking-wide text-coral/60 hover:text-coral"
                title="Remove this epic"
              >
                ✕
              </button>
            </>
          )}
        </div>
      </div>
      {showBrief && (
        edit ? (
          <textarea
            value={epic.brief}
            onChange={(e) => onEditBrief(e.target.value)}
            rows={4}
            className="mt-1 w-full rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-xs text-white/85 focus:border-aqua focus:outline-none"
          />
        ) : (
          <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-white/65">
            {epic.brief}
          </p>
        )
      )}
    </li>
  );
}
