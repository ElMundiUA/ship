"use client";

/**
 * Inbox routing-rule editor (RFC-0010 P2-16).
 *
 * Shared client component for the /settings/inbox-routing page. The
 * server component renders this in two modes:
 *
 *   - Create mode (no `rule` prop): the form posts to
 *     /api/inbox-routing/create. The handle field is editable.
 *   - Edit mode (`rule` provided): the form posts to
 *     /api/inbox-routing/update with a hidden rule_id. The handle is
 *     readonly because the backend does not allow renaming a handle
 *     (admins delete + recreate to rename, mirroring MemberGroup.key).
 *
 * The component is a client component for one reason only: the
 * "dynamic field" reveal driven by `target_type`. All target_*
 * inputs are conditionally *rendered* (not just visually hidden) so
 * the form's native serialization never carries stale values when
 * the admin toggles between user/group/strategy. The shape of the
 * submitted payload exactly matches the backend's per-target_type
 * cross-field invariants (see backend.app.api.v1.routes.inbox_routing
 * `_validate_cross_fields`).
 *
 * Note: `target_strategy` (a string naming a built-in resolver, e.g.
 * "round_robin" / "oncall" / "first") is a different field from
 * `assignment_strategy` (the typed enum that overrides a group's
 * default dispatcher). The form keeps them distinct.
 */

import { useState } from "react";

import type {
  InboxAssignmentStrategy,
  InboxRoutingRule,
  InboxRoutingTargetType,
} from "@/lib/inbox-types";

export type RuleEditorMember = {
  user_id: string;
  email: string;
  display_name: string | null;
  pending?: boolean;
};

export type RuleEditorGroup = {
  id: string;
  key: string;
  name: string;
};

export type RuleEditorProps = {
  workspaceId: string;
  members: RuleEditorMember[];
  groups: RuleEditorGroup[];
  /** When provided, switch into edit mode against this rule. */
  rule?: InboxRoutingRule;
  /** Create-mode only: prefill the handle input (e.g. from ?prefill=). */
  defaultHandle?: string;
};

const TARGET_TYPES: { value: InboxRoutingTargetType; label: string }[] = [
  { value: "user", label: "Single user" },
  { value: "group", label: "Operational group" },
  { value: "strategy", label: "Built-in strategy" },
];

const TARGET_TYPE_HELP: Record<InboxRoutingTargetType, string> = {
  user: "Always route to this workspace member. Best for single-owner handles like a security lead.",
  group:
    "Dereference to one of an operational group's members; the group's strategy decides who.",
  strategy:
    "Resolve at runtime via a built-in strategy (round_robin / oncall / first) without binding to a group.",
};

const STRATEGIES: { value: string; label: string }[] = [
  { value: "round_robin", label: "round_robin (rotate across all members)" },
  { value: "oncall", label: "oncall (currently on-call member)" },
  { value: "first", label: "first (oldest member by added-at)" },
];

const ASSIGNMENT_OVERRIDES: { value: "" | InboxAssignmentStrategy; label: string }[] = [
  { value: "", label: "use group default" },
  { value: "round_robin", label: "round_robin (override)" },
  { value: "oncall", label: "oncall (override)" },
  { value: "first", label: "first (override)" },
];

const FIELD_CLS =
  "w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40";
const READONLY_FIELD_CLS =
  "w-full rounded border border-white/10 bg-white/[0.02] px-2 py-1.5 text-sm text-white/65 outline-none cursor-not-allowed";
const LABEL_CLS =
  "mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55";

export function RuleEditor(props: RuleEditorProps) {
  const { workspaceId, members, groups, rule, defaultHandle } = props;
  const isEdit = rule !== undefined;
  const action = isEdit ? "/api/inbox-routing/update" : "/api/inbox-routing/create";

  // Default the create form to "group" when at least one group exists,
  // otherwise "user" — minimises the chance of submitting an inert
  // strategy-only rule on a fresh workspace.
  const defaultTargetType: InboxRoutingTargetType =
    rule?.target_type ?? (groups.length > 0 ? "group" : "user");

  const [targetType, setTargetType] = useState<InboxRoutingTargetType>(defaultTargetType);

  // Default selected target_user_id picks the rule's user (edit mode)
  // or the first non-pending member (create mode), so the dropdown is
  // never empty-on-submit when the workspace has any members at all.
  const sortedMembers = [...members].sort((a, b) => {
    const aPending = a.pending ? 1 : 0;
    const bPending = b.pending ? 1 : 0;
    if (aPending !== bPending) return aPending - bPending;
    const an = (a.display_name?.trim() || a.email).toLowerCase();
    const bn = (b.display_name?.trim() || b.email).toLowerCase();
    return an.localeCompare(bn);
  });

  const sortedGroups = [...groups].sort((a, b) => a.key.localeCompare(b.key));

  const noMembers = members.length === 0;
  const noGroups = groups.length === 0;

  const submitLabel = isEdit ? "Save rule" : "Create rule";

  return (
    <form action={action} method="POST" className="space-y-4">
      <input type="hidden" name="ws" value={workspaceId} suppressHydrationWarning />
      {isEdit && (
        <input type="hidden" name="rule_id" value={rule!.id} suppressHydrationWarning />
      )}

      <div>
        <label htmlFor="rule-handle" className={LABEL_CLS}>
          Handle
        </label>
        <input
          id="rule-handle"
          name="handle"
          required
          readOnly={isEdit}
          defaultValue={rule?.handle ?? defaultHandle ?? ""}
          pattern="^[a-z][a-z0-9_]*$"
          title="lowercase, starts with a letter, only letters/digits/underscores"
          placeholder="e.g. secops"
          suppressHydrationWarning
          className={isEdit ? READONLY_FIELD_CLS : FIELD_CLS}
        />
        <p className="mt-1 text-[11px] text-white/45">
          {isEdit
            ? "Handles are immutable. Delete + recreate to rename."
            : "Lowercase letters / digits / underscores; must start with a letter (matches MemberGroup.key)."}
        </p>
      </div>

      <div>
        <label htmlFor="rule-target-type" className={LABEL_CLS}>
          Target type
        </label>
        <select
          id="rule-target-type"
          name="target_type"
          value={targetType}
          onChange={(e) => setTargetType(e.target.value as InboxRoutingTargetType)}
          suppressHydrationWarning
          className={FIELD_CLS}
        >
          {TARGET_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
        <p className="mt-1 text-[11px] text-white/45">{TARGET_TYPE_HELP[targetType]}</p>
      </div>

      {targetType === "user" && (
        <div>
          <label htmlFor="rule-user" className={LABEL_CLS}>
            Target user
          </label>
          {noMembers ? (
            <p className="text-xs text-coral/85">
              No workspace members to pick from. Invite users under{" "}
              <a className="underline underline-offset-2" href="/members">
                /members
              </a>{" "}
              first.
            </p>
          ) : (
            <select
              id="rule-user"
              name="target_user_id"
              required
              defaultValue={rule?.target_user_id ?? sortedMembers[0]?.user_id ?? ""}
              suppressHydrationWarning
              className={FIELD_CLS}
            >
              {sortedMembers.map((m) => {
                const label = m.display_name?.trim() || m.email;
                const suffix = m.pending ? " (pending)" : "";
                return (
                  <option key={m.user_id} value={m.user_id}>
                    {label}
                    {suffix} — {m.email}
                  </option>
                );
              })}
            </select>
          )}
        </div>
      )}

      {targetType === "group" && (
        <>
          <div>
            <label htmlFor="rule-group" className={LABEL_CLS}>
              Target group
            </label>
            {noGroups ? (
              <p className="text-xs text-coral/85">
                No operational groups defined. Create one under{" "}
                <a
                  className="underline underline-offset-2"
                  href="/settings/groups"
                >
                  Settings → Operational groups
                </a>{" "}
                first.
              </p>
            ) : (
              <select
                id="rule-group"
                name="target_group_id"
                required
                defaultValue={rule?.target_group_id ?? sortedGroups[0]?.id ?? ""}
                suppressHydrationWarning
                className={FIELD_CLS}
              >
                {sortedGroups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.key} — {g.name}
                  </option>
                ))}
              </select>
            )}
          </div>
          <div>
            <label htmlFor="rule-assignment" className={LABEL_CLS}>
              Assignment strategy override (optional)
            </label>
            <select
              id="rule-assignment"
              name="assignment_strategy"
              defaultValue={rule?.assignment_strategy ?? ""}
              suppressHydrationWarning
              className={FIELD_CLS}
            >
              {ASSIGNMENT_OVERRIDES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-white/45">
              Leave on &ldquo;use group default&rdquo; to inherit the group&rsquo;s own
              strategy. Override only when this handle should dispatch differently
              from the group&rsquo;s baseline.
            </p>
          </div>
        </>
      )}

      {targetType === "strategy" && (
        <div>
          <label htmlFor="rule-strategy" className={LABEL_CLS}>
            Built-in strategy
          </label>
          <select
            id="rule-strategy"
            name="target_strategy"
            required
            defaultValue={rule?.target_strategy ?? "round_robin"}
            suppressHydrationWarning
            className={FIELD_CLS}
          >
            {STRATEGIES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <p className="mt-1 text-[11px] text-white/45">
            Built-in resolvers don&rsquo;t bind to a group; the resolver picks an
            owner from the workspace at intake time.
          </p>
        </div>
      )}

      <div className="flex items-center gap-2 rounded border border-white/10 bg-white/[0.02] px-3 py-2">
        <input
          id="rule-enabled"
          type="checkbox"
          name="is_enabled"
          value="1"
          defaultChecked={rule ? rule.is_enabled : true}
          suppressHydrationWarning
          className="h-4 w-4 rounded border-white/20 bg-white/10"
        />
        <label htmlFor="rule-enabled" className="text-xs text-white/80">
          Enabled — disabled rules are ignored at intake (the handle falls back to
          the built-in chain).
        </label>
      </div>

      <div className="flex items-center justify-between gap-2">
        <button
          type="submit"
          className="rounded-full bg-aqua/80 px-4 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua disabled:cursor-not-allowed disabled:opacity-40"
          disabled={
            (targetType === "user" && noMembers) ||
            (targetType === "group" && noGroups)
          }
        >
          {submitLabel}
        </button>
        {isEdit && (
          <p className="text-[11px] text-white/40">
            Saving keeps the same rule id; the handle stays bound continuously.
          </p>
        )}
      </div>
    </form>
  );
}
