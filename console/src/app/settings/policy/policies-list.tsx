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

import { ROLE_SCOPE_OPTIONS, roleLabel } from "./role-scope-options";

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
  const [appliesToRoles, setAppliesToRoles] = useState<string[]>(
    policy.applies_to_roles ?? [],
  );
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function reset() {
    setTitle(policy.title);
    setBody(policy.body);
    setAppliesToRoles(policy.applies_to_roles ?? []);
    setError(null);
  }

  function toggleRole(slug: string) {
    setAppliesToRoles((prev) =>
      prev.includes(slug) ? prev.filter((s) => s !== slug) : [...prev, slug],
    );
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
        const updated = await patch({
          title,
          body,
          // Empty array → null (back to global). The proxy preserves
          // the explicit ``null`` so the backend clears the column.
          applies_to_roles:
            appliesToRoles.length > 0 ? appliesToRoles : null,
        });
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
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {!policy.enabled ? <Badge tone="warn">disabled</Badge> : null}
            {policy.applies_to_roles && policy.applies_to_roles.length > 0 ? (
              policy.applies_to_roles.map((slug) => (
                <span
                  key={slug}
                  className="rounded-full border border-aqua/30 bg-aqua/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em] text-aqua"
                  title={`Applies only to ${roleLabel(slug)}`}
                >
                  {roleLabel(slug)}
                </span>
              ))
            ) : (
              <span
                className="rounded-full border border-white/15 bg-white/[0.04] px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em] text-white/55"
                title="Renders for every role and the Navigator chat"
              >
                global
              </span>
            )}
          </div>
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
          <div className="flex flex-col gap-2">
            <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
              Applies to roles
            </span>
            <p className="text-xs text-white/55">
              Empty selection ⇒ global. Pick one or more to scope the
              rule to those specialists only.
            </p>
            <div className="flex flex-wrap gap-2">
              {ROLE_SCOPE_OPTIONS.map((option) => {
                const active = appliesToRoles.includes(option.slug);
                return (
                  <button
                    key={option.slug}
                    type="button"
                    onClick={() => toggleRole(option.slug)}
                    className={`rounded-full border px-3 py-1 text-xs transition ${
                      active
                        ? "border-aqua/60 bg-aqua/15 text-aqua"
                        : "border-white/10 bg-white/[0.04] text-white/65 hover:border-white/25 hover:text-white"
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>
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
