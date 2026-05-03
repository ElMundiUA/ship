"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";

import { ButtonGhost, ButtonPrimary, Card } from "@/components/ui";
import type { ApiPolicy } from "@/lib/api/client";
import { cn } from "@/lib/cn";

import { ROLE_SCOPE_OPTIONS, roleLabel } from "./role-scope-options";

/**
 * Client island for the workspace-policies list.
 *
 * - Top filter bar: chip per role + "global" + "all" so admins can
 *   narrow a long list by scope without scrolling.
 * - Compact card row per policy with a toggle (enabled), inline
 *   pencil (edit) and trash (delete) icons in lieu of full buttons.
 * - Inline edit form keeps the role-scope picker so a single click
 *   re-targets the rule.
 *
 * Every PATCH/DELETE round-trips through ``/api/policies`` and
 * returns the full updated row (or 204), so we replace in place
 * instead of refetching the list.
 */
export function PoliciesList({
  workspaceId,
  policies: initial,
}: {
  workspaceId: string;
  policies: ApiPolicy[];
}) {
  const [policies, setPolicies] = useState<ApiPolicy[]>(initial);
  // ``null`` = all, ``"global"`` = unscoped only, otherwise role slug.
  const [filter, setFilter] = useState<string | null>(null);

  function replace(updated: ApiPolicy) {
    setPolicies((prev) =>
      prev.map((p) => (p.id === updated.id ? updated : p)),
    );
  }

  function removeLocal(id: string) {
    setPolicies((prev) => prev.filter((p) => p.id !== id));
  }

  // Pre-compute counts so the filter chips show "(N)" — admins can
  // tell at a glance whether a scope has any rules before clicking.
  const counts = useMemo(() => {
    const map = new Map<string, number>();
    let globalCount = 0;
    for (const p of policies) {
      if (!p.applies_to_roles || p.applies_to_roles.length === 0) {
        globalCount += 1;
      } else {
        for (const slug of p.applies_to_roles) {
          map.set(slug, (map.get(slug) ?? 0) + 1);
        }
      }
    }
    return { byRole: map, global: globalCount, total: policies.length };
  }, [policies]);

  const visible = useMemo(() => {
    if (filter === null) return policies;
    if (filter === "global") {
      return policies.filter(
        (p) => !p.applies_to_roles || p.applies_to_roles.length === 0,
      );
    }
    return policies.filter((p) =>
      (p.applies_to_roles ?? []).includes(filter),
    );
  }, [policies, filter]);

  return (
    <div className="flex flex-col gap-3">
      <FilterBar
        active={filter}
        onChange={setFilter}
        total={counts.total}
        globalCount={counts.global}
        roleCounts={counts.byRole}
      />
      {visible.length === 0 ? (
        <p className="px-1 py-6 text-center text-xs text-white/45">
          No policies match this filter.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {visible.map((policy) => (
            <PolicyRow
              key={policy.id}
              policy={policy}
              workspaceId={workspaceId}
              onReplace={replace}
              onRemove={() => removeLocal(policy.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FilterBar({
  active,
  onChange,
  total,
  globalCount,
  roleCounts,
}: {
  active: string | null;
  onChange: (next: string | null) => void;
  total: number;
  globalCount: number;
  roleCounts: Map<string, number>;
}) {
  // Roles get rendered in the same order as ROLE_SCOPE_OPTIONS, then
  // filter to only the ones that actually have rules — there's no
  // value showing a "Game balance reviewer (0)" chip when nobody
  // scoped a rule there.
  const roleEntries = ROLE_SCOPE_OPTIONS.filter(
    (opt) => (roleCounts.get(opt.slug) ?? 0) > 0,
  );

  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
      <FilterChip
        active={active === null}
        onClick={() => onChange(null)}
        label={`All · ${total}`}
      />
      <FilterChip
        active={active === "global"}
        onClick={() => onChange("global")}
        label={`Global · ${globalCount}`}
      />
      <span className="mx-1 h-4 w-px bg-white/10" aria-hidden />
      {roleEntries.map((opt) => (
        <FilterChip
          key={opt.slug}
          active={active === opt.slug}
          onClick={() => onChange(opt.slug)}
          label={`${opt.label} · ${roleCounts.get(opt.slug) ?? 0}`}
        />
      ))}
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full px-2.5 py-1 text-[11px] font-medium transition",
        active
          ? "bg-aqua/15 text-aqua ring-1 ring-aqua/40"
          : "text-white/55 hover:bg-white/[0.06] hover:text-white",
      )}
    >
      {label}
    </button>
  );
}

function PolicyRow({
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

  const scopeChips = (() => {
    const slugs =
      policy.applies_to_roles && policy.applies_to_roles.length > 0
        ? policy.applies_to_roles
        : null;
    if (slugs === null) {
      return (
        <ScopeChip tone="muted" title="Renders for every role + Navigator chat">
          global
        </ScopeChip>
      );
    }
    return slugs.map((slug) => (
      <ScopeChip
        key={slug}
        tone="aqua"
        title={`Applies only to ${roleLabel(slug)}`}
      >
        {roleLabel(slug)}
      </ScopeChip>
    ));
  })();

  return (
    <Card padded={false} className="overflow-hidden">
      <div className="flex items-center gap-3 px-3.5 py-2.5">
        <Toggle
          checked={policy.enabled}
          disabled={pending}
          onClick={toggleEnabled}
          label={policy.enabled ? "Disable policy" : "Enable policy"}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3
              className={cn(
                "truncate text-sm font-semibold",
                policy.enabled ? "text-white" : "text-white/45 line-through",
              )}
            >
              {policy.title}
            </h3>
            <div className="flex shrink-0 flex-wrap items-center gap-1">
              {scopeChips}
            </div>
          </div>
          <div className="mt-0.5 text-[10px] text-white/35">
            sort {policy.sort_order} · updated{" "}
            {new Date(policy.updated_at).toLocaleString()}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <IconButton
            label={editing ? "Cancel edit" : "Edit policy"}
            onClick={() => {
              if (editing) reset();
              setEditing((v) => !v);
            }}
          >
            {editing ? <CloseIcon /> : <PencilIcon />}
          </IconButton>
          <IconButton
            label="Delete policy"
            tone="danger"
            disabled={pending}
            onClick={deletePolicy}
          >
            <TrashIcon />
          </IconButton>
        </div>
      </div>

      {editing ? (
        <div className="flex flex-col gap-3 border-t border-white/[0.06] px-3.5 py-3">
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
            <div className="flex flex-wrap gap-1.5">
              {ROLE_SCOPE_OPTIONS.map((option) => {
                const active = appliesToRoles.includes(option.slug);
                return (
                  <button
                    key={option.slug}
                    type="button"
                    onClick={() => toggleRole(option.slug)}
                    className={cn(
                      "rounded-full border px-2.5 py-0.5 text-[11px] transition",
                      active
                        ? "border-aqua/60 bg-aqua/15 text-aqua"
                        : "border-white/10 bg-white/[0.04] text-white/65 hover:border-white/25 hover:text-white",
                    )}
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
            "whitespace-pre-wrap border-t border-white/[0.06] bg-black/20 px-3.5 py-2.5 font-mono text-[11px] leading-relaxed text-white/70",
            !policy.enabled && "opacity-50",
          )}
        >
          {policy.body}
        </div>
      )}

      {error ? (
        <div className="border-t border-white/[0.06] bg-rose-500/10 px-3.5 py-1.5 text-[11px] text-rose-200">
          {error}
        </div>
      ) : null}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Small visual primitives
// ---------------------------------------------------------------------------

function ScopeChip({
  children,
  tone,
  title,
}: {
  children: React.ReactNode;
  tone: "muted" | "aqua";
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "rounded-full px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.1em]",
        tone === "aqua"
          ? "bg-aqua/12 text-aqua ring-1 ring-aqua/30"
          : "bg-white/[0.05] text-white/45 ring-1 ring-white/10",
      )}
    >
      {children}
    </span>
  );
}

function Toggle({
  checked,
  disabled,
  onClick,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors",
        checked
          ? "border-aqua/50 bg-aqua/35 hover:bg-aqua/45"
          : "border-white/15 bg-white/[0.06] hover:bg-white/[0.1]",
        disabled && "cursor-not-allowed opacity-40",
      )}
    >
      <span
        className={cn(
          "inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform",
          checked ? "translate-x-[18px]" : "translate-x-[2px]",
        )}
      />
    </button>
  );
}

function IconButton({
  children,
  label,
  onClick,
  disabled,
  tone = "neutral",
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: "neutral" | "danger";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={cn(
        "inline-flex h-7 w-7 items-center justify-center rounded-md text-white/55 transition",
        tone === "danger"
          ? "hover:bg-rose-500/15 hover:text-rose-300"
          : "hover:bg-white/[0.08] hover:text-white",
        disabled && "cursor-not-allowed opacity-40",
      )}
    >
      {children}
    </button>
  );
}

function PencilIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}
