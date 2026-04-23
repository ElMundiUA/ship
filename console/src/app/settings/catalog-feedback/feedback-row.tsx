"use client";

/**
 * MIGRATED: feedback-row moved to /settings/catalog-feedback per RFC-0010 P2-18.
 *
 * One feedback row with inline status + linked-PR edit.
 *
 * Tiny by design — triage is a two-field form (status + linked PR
 * URL) so the reviewer can mark something ``triaged`` without
 * leaving the list.
 */

import { useState, useTransition } from "react";

import { Badge } from "@/components/ui";
import type {
  ApiArtifactFeedback,
  ApiArtifactFeedbackStatus,
} from "@/lib/api/client";

const STATUS_TONE: Record<ApiArtifactFeedbackStatus, "neutral" | "ok" | "warn" | "info"> = {
  open: "warn",
  triaged: "info",
  merged: "ok",
  closed: "neutral",
};

export function FeedbackRow({
  workspaceId,
  item,
}: {
  workspaceId: string;
  item: ApiArtifactFeedback;
}) {
  const [status, setStatus] = useState<ApiArtifactFeedbackStatus>(item.status);
  const [linkedPr, setLinkedPr] = useState(item.linked_pr_url ?? "");
  const [editing, setEditing] = useState(false);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    startTransition(async () => {
      setError(null);
      const res = await fetch(
        `/api/chat/artifact-feedback/${encodeURIComponent(item.id)}?workspace_id=${encodeURIComponent(workspaceId)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            status,
            linked_pr_url: linkedPr.trim() || null,
          }),
        },
      );
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        setError(text || `HTTP ${res.status}`);
        return;
      }
      setEditing(false);
    });
  };

  return (
    <li className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-white/55">
        <Badge tone={STATUS_TONE[status]}>{status}</Badge>
        <code className="text-white/70">{item.artifact_id}</code>
        <span className="ml-auto">
          {new Date(item.created_at).toLocaleString()}
        </span>
      </div>
      <p className="mt-2 whitespace-pre-wrap text-sm text-white/85">
        {item.body}
      </p>
      {item.linked_pr_url && !editing ? (
        <p className="mt-2 text-[11px]">
          <a
            href={item.linked_pr_url}
            target="_blank"
            rel="noreferrer"
            className="text-aqua hover:underline"
          >
            {item.linked_pr_url}
          </a>
        </p>
      ) : null}

      {editing ? (
        <div className="mt-3 space-y-2 rounded-lg border border-white/10 bg-black/30 p-3">
          <label className="block text-[11px] font-semibold uppercase tracking-wider text-white/55">
            Status
            <select
              value={status}
              onChange={(e) =>
                setStatus(e.target.value as ApiArtifactFeedbackStatus)
              }
              className="mt-1 w-full rounded-md border border-white/10 bg-black/40 px-2 py-1 text-sm text-white"
            >
              <option value="open">open</option>
              <option value="triaged">triaged</option>
              <option value="merged">merged</option>
              <option value="closed">closed</option>
            </select>
          </label>
          <label className="block text-[11px] font-semibold uppercase tracking-wider text-white/55">
            Linked PR URL
            <input
              type="url"
              value={linkedPr}
              onChange={(e) => setLinkedPr(e.target.value)}
              placeholder="https://github.com/.../pull/123"
              className="mt-1 w-full rounded-md border border-white/10 bg-black/40 px-2 py-1 text-sm text-white"
            />
          </label>
          {error ? (
            <p className="text-[11px] text-rose-300">{error}</p>
          ) : null}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={save}
              disabled={pending}
              className="rounded-md bg-aqua px-3 py-1 text-xs font-semibold text-black hover:bg-aqua/90 disabled:opacity-50"
            >
              {pending ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setStatus(item.status);
                setLinkedPr(item.linked_pr_url ?? "");
              }}
              className="rounded-md border border-white/10 px-3 py-1 text-xs text-white/70 hover:border-white/30 hover:text-white"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="rounded-md border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-white/70 hover:border-white/30 hover:text-white"
          >
            Triage
          </button>
        </div>
      )}
    </li>
  );
}
