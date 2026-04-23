"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import {
  Badge,
  ButtonDanger,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
} from "@/components/ui";
import type { ApiPolicy } from "@/lib/api/client";
import { cn } from "@/lib/cn";

/**
 * Client island for the workspace-policies list.
 *
 * Inline edit (textarea), enable/disable toggle and delete — all
 * three POST/PATCH/DELETE responses round-trip through
 * ``/api/policies`` and return the full updated row, so we replace
 * in place rather than refetching the list.
 */
export function PoliciesList({
  workspaceId,
  policies: initial,
}: {
  workspaceId: string;
  policies: ApiPolicy[];
}) {
  const [policies, setPolicies] = useState<ApiPolicy[]>(initial);

  function replace(updated: ApiPolicy) {
    setPolicies((prev) =>
      prev.map((p) => (p.id === updated.id ? updated : p)),
    );
  }

  function removeLocal(id: string) {
    setPolicies((prev) => prev.filter((p) => p.id !== id));
  }

  return (
    <div className="flex flex-col gap-4">
      {policies.map((policy) => (
        <PolicyCard
          key={policy.id}
          policy={policy}
          workspaceId={workspaceId}
          onReplace={replace}
          onRemove={() => removeLocal(policy.id)}
        />
      ))}
    </div>
  );
}

function PolicyCard({
  policy,
  workspaceId,
  onReplace,
  onRemove,
}: {
  policy: ApiPolicy;
  workspaceId: string;
  onReplace: (p: ApiPolicy) => void;
  onRemove: () => void;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(policy.title);
  const [body, setBody] = useState(policy.body);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setTitle(policy.title);
    setBody(policy.body);
    setError(null);
  }

  async function patch(payload: Record<string, unknown>) {
    const res = await fetch(`/api/policies/${policy.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ workspaceId, ...payload }),
    });
    if (!res.ok) {
      const eb = await res.json().catch(() => ({}));
      throw new Error(eb?.error ?? `HTTP ${res.status}`);
    }
    return (await res.json()) as ApiPolicy;
  }

  function save() {
    startTransition(async () => {
      setError(null);
      try {
        if (!title.trim() || !body.trim()) {
          throw new Error("Title and body are required");
        }
        const updated = await patch({ title, body });
        onReplace(updated);
        setEditing(false);
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to save");
      }
    });
  }

  function toggleEnabled() {
    startTransition(async () => {
      setError(null);
      try {
        const updated = await patch({ enabled: !policy.enabled });
        onReplace(updated);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to toggle");
      }
    });
  }

  function deletePolicy() {
    if (
      !window.confirm(
        `Delete policy "${policy.title}"? Agent prompts will stop including this rule immediately.`,
      )
    ) {
      return;
    }
    startTransition(async () => {
      setError(null);
      try {
        const res = await fetch(
          `/api/policies/${policy.id}?workspaceId=${encodeURIComponent(workspaceId)}`,
          { method: "DELETE" },
        );
        if (!res.ok && res.status !== 204) {
          const eb = await res.json().catch(() => ({}));
          throw new Error(eb?.error ?? `HTTP ${res.status}`);
        }
        onRemove();
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to delete");
      }
    });
  }

  return (
    <Card padded={false}>
      <div className="flex flex-wrap items-start gap-4 border-b border-white/[0.08] px-5 py-4">
        <div className="min-w-0 flex-1">
          <CardHeader
            title={policy.title}
            subtitle={
              <span className="text-[11px] text-white/45">
                sort {policy.sort_order} · updated{" "}
                {new Date(policy.updated_at).toLocaleString()}
              </span>
            }
          />
          {!policy.enabled ? (
            <div className="mt-2">
              <Badge tone="warn">disabled</Badge>
            </div>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <ButtonGhost onClick={toggleEnabled}>
            {pending ? "…" : policy.enabled ? "Disable" : "Enable"}
          </ButtonGhost>
          <ButtonGhost
            onClick={() => {
              if (editing) reset();
              setEditing((v) => !v);
            }}
          >
            {editing ? "Cancel" : "Edit"}
          </ButtonGhost>
          <ButtonDanger onClick={deletePolicy}>
            {pending ? "…" : "Delete"}
          </ButtonDanger>
        </div>
      </div>

      {editing ? (
        <div className="flex flex-col gap-3 px-5 py-4">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
              Title
            </span>
            <input
              type="text"
              value={title}
              maxLength={160}
              onChange={(e) => setTitle(e.target.value)}
              className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/50"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
              Body (markdown)
            </span>
            <textarea
              value={body}
              rows={8}
              onChange={(e) => setBody(e.target.value)}
              className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 font-mono text-xs leading-relaxed text-white outline-none focus:border-aqua/50"
            />
          </label>
          <div className="flex justify-end gap-2">
            <ButtonGhost
              onClick={() => {
                reset();
                setEditing(false);
              }}
            >
              Cancel
            </ButtonGhost>
            <ButtonPrimary onClick={save}>
              {pending ? "Saving…" : "Save"}
            </ButtonPrimary>
          </div>
        </div>
      ) : (
        <div
          className={cn(
            "whitespace-pre-wrap px-5 py-4 font-mono text-xs leading-relaxed text-white/75",
            !policy.enabled && "opacity-60",
          )}
        >
          {policy.body}
        </div>
      )}

      {error ? (
        <div className="border-t border-white/[0.08] bg-rose-500/10 px-5 py-2 text-xs text-rose-200">
          {error}
        </div>
      ) : null}
    </Card>
  );
}
