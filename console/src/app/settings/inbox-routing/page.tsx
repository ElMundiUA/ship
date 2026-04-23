/**
 * Inbox routing rules settings page (RFC-0010 ticket P2-16).
 *
 * Workspace-scoped admin surface for `inbox_routing_rules` — the
 * mapping that turns a Play's symbolic handle (`secops`,
 * `repo_maintainer`, `incident_commander`, …) into a concrete
 * dispatch target (a single user, an operational MemberGroup, or one
 * of the built-in resolver strategies).
 *
 * Two-pane layout (matches /settings/groups):
 *
 *   - Left: configuration health banner + rules list + create form
 *   - Right: editor for the currently-selected rule + dry-run preview
 *
 * Vocabulary surfaced by the health banner:
 *   - bound    — handle has at least one rule (will route on intake)
 *   - used     — handle is referenced by an emit rule in the catalog
 *   - orphaned — bound but not used (rule will never fire)
 *   - unbound  — used but not bound (intake falls back to admin)
 *
 * The dry-run preview wraps the resolver in a SAVEPOINT and rolls it
 * back unconditionally, so admins can poke "what would this do?"
 * without nudging round_robin pointers — the preview is side-effect
 * free by design and round-trips through the URL so the result lands
 * in the page's searchParams (?preview=&preview_user=&preview_reason=).
 */

import { AppShell } from "@/components/app-shell";
import { RuleEditor } from "@/components/inbox/rule-editor";
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  LiveBanner,
  MockBanner,
} from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  getInboxRoutingHandles,
  getInboxRoutingRule,
  getMe,
  isApiConfigured,
  listInboxGroups,
  listInboxRoutingRules,
  listMembers,
  listWorkspaces,
} from "@/lib/api/client";
import type { ApiInboxGroup } from "@/lib/api/client";
import type { ApiMember, ApiUser, ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import type {
  InboxRoutingHandlesOut,
  InboxRoutingRule,
  InboxRoutingRuleDetail,
} from "@/lib/inbox-types";

export const dynamic = "force-dynamic";

// ---------------------------------------------------------------------------
// Error / reason copy
// ---------------------------------------------------------------------------

function errorMessage(code: string): string {
  switch (code) {
    case "bad_input":
      return "Missing required fields. Handle and target type are both required.";
    case "forbidden":
      return "Admin role required to manage inbox routing rules.";
    case "not_found":
      return "Routing rule not found — it may have been deleted in another tab.";
    case "duplicate":
      return "A rule for that handle already exists in this workspace. Edit or delete it first; handles are unique per workspace.";
    case "validation_failed":
      return "Backend rejected the input — check the field values and the target/strategy combination.";
    case "target_user_not_member":
      return "That user is not a member of this workspace. Invite them under /members first.";
    case "target_group_not_workspace":
      return "That group does not exist in this workspace. Create it under Settings → Operational groups first.";
    case "api_unavailable":
      return "Backend is unreachable. Try again in a moment.";
    case "unknown":
      return "Something went wrong applying the change. Try again or refresh.";
    default:
      return `Couldn't apply the change (${code}). Try again or refresh.`;
  }
}

const REASON_HELP: Record<string, string> = {
  "rule:user": "A routing rule explicitly named this user.",
  "rule:group": "A rule pointed at a group; the group's strategy picked the owner.",
  "rule:strategy": "A rule pointed at a built-in strategy.",
  "fallback:workspace_admin":
    "No routing rule matched. The first workspace admin caught it.",
  "fallback:workspace_owner":
    "No admin available. The workspace owner caught it as last resort.",
  "fallback:none": "No owner could be resolved. The item will sit unassigned.",
};

function reasonHelp(reason: string): string {
  if (REASON_HELP[reason]) return REASON_HELP[reason];
  if (reason.startsWith("rule:")) return "A routing rule resolved this handle.";
  if (reason.startsWith("fallback:"))
    return "No matching rule; built-in fallback chain answered.";
  return reason;
}

// ---------------------------------------------------------------------------
// Loader (live ↔ mock)
// ---------------------------------------------------------------------------

type LoadedData = {
  source: "live";
  workspace: ApiWorkspace;
  rules: InboxRoutingRule[];
  handles: InboxRoutingHandlesOut;
  groups: ApiInboxGroup[];
  members: ApiMember[];
  selected: InboxRoutingRuleDetail | null;
  me: ApiUser | null;
};

type Mode = LoadedData | { source: "mock"; reason: string };

async function load(selectedRuleId: string | null): Promise<Mode> {
  if (!isApiConfigured())
    return { source: "mock", reason: "SHIP_API_URL is not set" };
  const token = await getSessionToken();
  if (!token)
    return { source: "mock", reason: "Sign in to manage routing rules" };

  try {
    const ws = await listWorkspaces(token);
    if (ws.length === 0)
      return {
        source: "mock",
        reason: "Create a workspace first to manage routing rules",
      };
    const workspace = ws[0];
    const [rules, handles, groups, members, me] = await Promise.all([
      listInboxRoutingRules(workspace.id, token),
      getInboxRoutingHandles(workspace.id, token).catch(
        () => ({
          bound_handles: [],
          used_handles: [],
          orphaned_handles: [],
          unbound_handles: [],
        }) satisfies InboxRoutingHandlesOut,
      ),
      listInboxGroups(workspace.id, token),
      listMembers(workspace.id, token),
      getMe(token).catch(() => null as ApiUser | null),
    ]);

    let selected: InboxRoutingRuleDetail | null = null;
    if (selectedRuleId) {
      try {
        selected = await getInboxRoutingRule(workspace.id, selectedRuleId, token);
      } catch {
        selected = null;
      }
    }

    return {
      source: "live",
      workspace,
      rules,
      handles,
      groups,
      members,
      selected,
      me,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      return { source: "mock", reason: "Session expired — sign in again" };
    if (err instanceof ApiUnavailableError)
      return { source: "mock", reason: "Backend unreachable" };
    return { source: "mock", reason: "Backend returned an error" };
  }
}

function meToShellUser(me: ApiUser | null) {
  if (!me) return null;
  const name = me.display_name?.trim() || me.email;
  const parts = name.split(/[\s@.]+/).filter(Boolean);
  const initials =
    parts.length >= 2
      ? (parts[0][0] + parts[1][0]).toUpperCase()
      : (parts[0]?.slice(0, 2) ?? "??").toUpperCase();
  return { name, email: me.email, initials };
}

// ---------------------------------------------------------------------------
// Filters (?filter=bound|unbound|orphaned|used)
// ---------------------------------------------------------------------------

type RuleFilter = "all" | "bound" | "unbound" | "orphaned" | "used";

function parseFilter(raw: string | null): RuleFilter {
  if (raw === "bound" || raw === "unbound" || raw === "orphaned" || raw === "used")
    return raw;
  return "all";
}

const FILTER_LABEL: Record<RuleFilter, string> = {
  all: "All rules",
  bound: "Bound handles",
  used: "Used handles",
  orphaned: "Orphaned (will never fire)",
  unbound: "Unbound (will fall back to admin)",
};

function applyFilter(
  rules: InboxRoutingRule[],
  handles: InboxRoutingHandlesOut,
  filter: RuleFilter,
): InboxRoutingRule[] {
  if (filter === "all") return rules;
  if (filter === "bound")
    return rules.filter((r) => handles.bound_handles.includes(r.handle));
  if (filter === "used")
    return rules.filter((r) => handles.used_handles.includes(r.handle));
  if (filter === "orphaned")
    return rules.filter((r) => handles.orphaned_handles.includes(r.handle));
  // For unbound: rules don't include unbound handles by definition.
  // Show nothing here; the chip's primary purpose is to surface the
  // missing handles in the banner above.
  return [];
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default async function InboxRoutingSettingsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;

  const selectedRuleId = typeof params.rule === "string" ? params.rule : null;
  const errorCode = typeof params.error === "string" ? params.error : null;
  const justDeleted = params.deleted === "1";
  const justSaved = params.saved === "1";
  const justCreated = typeof params.created === "string" ? params.created : null;
  const previewHandle = typeof params.preview === "string" ? params.preview : null;
  const previewUser =
    typeof params.preview_user === "string" ? params.preview_user : null;
  const previewReason =
    typeof params.preview_reason === "string" ? params.preview_reason : null;
  const previewIntake =
    typeof params.preview_intake === "string" ? params.preview_intake : null;
  const previewIsError = params.preview_error === "1";
  const filter = parseFilter(
    typeof params.filter === "string" ? params.filter : null,
  );

  const data = await load(selectedRuleId);

  if (data.source === "mock") {
    return <MockView reason={data.reason} errorCode={errorCode} />;
  }

  const { workspace, rules, handles, groups, members, selected, me } = data;
  const visibleRules = applyFilter(rules, handles, filter);

  return (
    <AppShell
      kicker="access"
      title="Inbox routing"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      me={meToShellUser(me)}
    >
      <LiveBanner workspace={workspace.slug} />

      {errorCode && (
        <div className="mb-3 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
          {errorMessage(errorCode)}
        </div>
      )}
      {justDeleted && !errorCode && (
        <div className="mb-3 rounded-xl border border-emerald-400/30 bg-emerald-500/[0.06] px-3 py-2 text-xs text-emerald-300">
          Routing rule deleted. The handle now falls back to the built-in chain
          (workspace admin → workspace owner) at intake.
        </div>
      )}
      {justSaved && !errorCode && (
        <div className="mb-3 rounded-xl border border-emerald-400/30 bg-emerald-500/[0.06] px-3 py-2 text-xs text-emerald-300">
          Rule saved.
        </div>
      )}
      {justCreated && !errorCode && (
        <div className="mb-3 rounded-xl border border-emerald-400/30 bg-emerald-500/[0.06] px-3 py-2 text-xs text-emerald-300">
          Created routing rule for <code className="font-mono">{justCreated}</code>.
        </div>
      )}

      {previewHandle && (
        <PreviewBanner
          handle={previewHandle}
          intake={previewIntake}
          user={previewUser}
          reason={previewReason}
          isError={previewIsError}
        />
      )}

      {/* Configuration health banner */}
      <HealthBanner handles={handles} activeFilter={filter} />

      {rules.length === 0 && groups.length === 0 ? (
        <EmptyState
          title="Get started with inbox routing"
          body="Routing rules dereference symbolic handles (e.g. secops, repo_maintainer) into concrete owners. Most rules point at an operational group; create a group first under Settings → Operational groups, then come back here to bind handles to it."
          action={
            <a
              href="/settings/groups"
              className="rounded-full bg-aqua/80 px-4 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua"
            >
              Create operational groups →
            </a>
          }
        />
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_1.4fr]">
          {/* Left: rules list + create form */}
          <Card padded={false} className="overflow-hidden">
            <CardHeader
              className="px-5 pt-5"
              title="Rules"
              subtitle={
                filter === "all"
                  ? "Ordered by handle, then created_at. The resolver matches enabled rules first."
                  : `Filtered: ${FILTER_LABEL[filter]}`
              }
              action={
                filter !== "all" ? (
                  <a
                    href="/settings/inbox-routing"
                    className="text-[11px] text-aqua underline underline-offset-2"
                  >
                    Clear filter
                  </a>
                ) : undefined
              }
            />

            {visibleRules.length === 0 ? (
              <div className="px-5 pb-5">
                <EmptyState
                  title={
                    filter === "unbound"
                      ? "Nothing to list here"
                      : filter === "all"
                        ? "No routing rules yet"
                        : "No matching rules"
                  }
                  body={
                    filter === "unbound"
                      ? "Unbound handles by definition have no rules. See the configuration health chips above for the list of unbound handles that fall back to the admin chain."
                      : filter === "all"
                        ? "Use the form below to map a symbolic handle to a workspace member, an operational group, or a built-in strategy."
                        : "No rules match this filter. Clear the filter to see all rules."
                  }
                />
              </div>
            ) : (
              <ul className="divide-y divide-white/5">
                {visibleRules.map((r) => {
                  const active = selected?.id === r.id;
                  return (
                    <li
                      key={r.id}
                      className={`flex items-center justify-between gap-3 px-5 py-3 transition ${
                        active
                          ? "border-l-2 border-aqua bg-aqua/10"
                          : "hover:bg-white/[0.04]"
                      }`}
                    >
                      <a
                        href={`/settings/inbox-routing?rule=${encodeURIComponent(r.id)}`}
                        className="min-w-0 flex-1"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <code className="text-[11px] font-bold text-aqua">
                            {r.handle}
                          </code>
                          <TargetChip rule={r} groups={groups} />
                          {!r.is_enabled && <Badge tone="warn">disabled</Badge>}
                          {handles.orphaned_handles.includes(r.handle) && (
                            <Badge tone="neutral">orphan</Badge>
                          )}
                        </div>
                      </a>
                      <form
                        action="/api/inbox-routing/preview"
                        method="POST"
                        className="shrink-0"
                      >
                        <input
                          type="hidden"
                          name="ws"
                          value={workspace.id}
                          suppressHydrationWarning
                        />
                        <input
                          type="hidden"
                          name="handle"
                          value={r.handle}
                          suppressHydrationWarning
                        />
                        <input
                          type="hidden"
                          name="rule_id"
                          value={r.id}
                          suppressHydrationWarning
                        />
                        <button
                          type="submit"
                          className="rounded-full border border-white/15 bg-white/[0.04] px-2.5 py-0.5 text-[10px] font-semibold text-white/70 transition hover:border-aqua/40 hover:bg-aqua/10 hover:text-aqua"
                          title="Dry-run resolve this handle (no side effects)"
                        >
                          Preview
                        </button>
                      </form>
                    </li>
                  );
                })}
              </ul>
            )}

            {/* Create form (always shown) */}
            <div className="border-t border-white/5 bg-white/[0.02] px-5 py-4">
              <h4 className="mb-3 text-[10px] font-bold uppercase tracking-widest text-white/55">
                New routing rule
              </h4>
              <RuleEditor
                workspaceId={workspace.id}
                members={members}
                groups={groups}
              />
            </div>
          </Card>

          {/* Right: editor + preview */}
          <div className="space-y-5">
            <Card padded={false} className="overflow-hidden">
              {selected ? (
                <SelectedRuleEditor
                  workspace={workspace}
                  rule={selected}
                  members={members}
                  groups={groups}
                />
              ) : (
                <div className="p-5">
                  <CardHeader
                    title="Pick a rule on the left"
                    subtitle="Or create one from the form below the list."
                  />
                  <p className="text-sm text-white/65">
                    Each rule maps a symbolic handle (like{" "}
                    <code>secops</code> or <code>repo_maintainer</code>) to
                    either a single user, an operational{" "}
                    <a
                      className="text-aqua underline underline-offset-2"
                      href="/settings/groups"
                    >
                      group
                    </a>
                    , or a built-in strategy. Disabled rules are skipped at
                    intake; the handle then falls back to the workspace-admin
                    chain.
                  </p>
                </div>
              )}
            </Card>

            <Card>
              <CardHeader
                title="Dry-run preview"
                subtitle="Resolve any handle as if a Play emitted it now. Side-effect free — round_robin pointers are not nudged."
              />
              <form
                action="/api/inbox-routing/preview"
                method="POST"
                className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2"
              >
                <input
                  type="hidden"
                  name="ws"
                  value={workspace.id}
                  suppressHydrationWarning
                />
                <label className="block">
                  <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
                    Handle
                  </span>
                  <input
                    name="handle"
                    placeholder="e.g. secops"
                    required
                    pattern="^[a-z][a-z0-9_]*$"
                    title="lowercase, starts with a letter, only letters/digits/underscores"
                    suppressHydrationWarning
                    className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
                  />
                </label>
                <button
                  type="submit"
                  className="rounded-full bg-aqua/80 px-4 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua"
                >
                  Preview
                </button>
              </form>
            </Card>
          </div>
        </div>
      )}
    </AppShell>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function PreviewBanner({
  handle,
  intake,
  user,
  reason,
  isError,
}: {
  handle: string;
  intake: string | null;
  user: string | null;
  reason: string | null;
  isError: boolean;
}) {
  const ownerLabel = user && user.length > 0 ? user : "unresolved → fallback";
  return (
    <div
      className={`mb-3 rounded-xl border px-3 py-2 text-xs ${
        isError
          ? "border-coral/30 bg-coral/[0.06] text-coral/95"
          : "border-sky-400/30 bg-sky-500/[0.06] text-sky-200"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-white/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest">
          preview
        </span>
        <code className="font-mono text-[12px]">{handle}</code>
        <span className="text-white/55">→</span>
        <span className="font-semibold text-white">{ownerLabel}</span>
        {intake && intake !== handle && (
          <>
            <span className="text-white/40">via</span>
            <code className="font-mono text-[11px] text-white/80">{intake}</code>
          </>
        )}
        {reason && (
          <span
            className="ml-auto cursor-help text-[11px] text-white/65 underline decoration-dotted underline-offset-2"
            title={reasonHelp(reason)}
          >
            {reason}
          </span>
        )}
      </div>
    </div>
  );
}

function HealthBanner({
  handles,
  activeFilter,
}: {
  handles: InboxRoutingHandlesOut;
  activeFilter: RuleFilter;
}) {
  const boundCount = handles.bound_handles.length;
  const usedCount = handles.used_handles.length;
  const orphanedCount = handles.orphaned_handles.length;
  const unboundCount = handles.unbound_handles.length;

  return (
    <Card className="mb-5">
      <div className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/45">
        Configuration health
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Chip
          tone="ok"
          href="/settings/inbox-routing?filter=bound"
          active={activeFilter === "bound"}
          label={`${boundCount} handle${boundCount === 1 ? "" : "s"} routed`}
          help="At least one rule exists for these handles. Intake dispatches via the rule, not the fallback chain."
        />
        <Chip
          tone={unboundCount > 0 ? "warn" : "neutral"}
          href="/settings/inbox-routing?filter=unbound"
          active={activeFilter === "unbound"}
          label={`${unboundCount} unbound`}
          help="Handles referenced by the catalog but with no rule. Intake falls back to the workspace-admin chain. Bind a rule to silence the noise."
        />
        <Chip
          tone={orphanedCount > 0 ? "neutral" : "neutral"}
          href="/settings/inbox-routing?filter=orphaned"
          active={activeFilter === "orphaned"}
          label={`${orphanedCount} orphaned`}
          help="Rules exist for handles the catalog never emits. The rule will never fire — safe to delete."
        />
        <Chip
          tone="info"
          href="/settings/inbox-routing?filter=used"
          active={activeFilter === "used"}
          label={`${usedCount} catalog handle${usedCount === 1 ? "" : "s"}`}
          help="Total distinct handles emitted by the shipped profile catalog. Anything not in this set will never reach the inbox today."
        />
        {activeFilter !== "all" && (
          <a
            href="/settings/inbox-routing"
            className="ml-auto text-[11px] text-aqua underline underline-offset-2"
          >
            Clear filter
          </a>
        )}
      </div>
    </Card>
  );
}

function Chip({
  tone,
  href,
  active,
  label,
  help,
}: {
  tone: "ok" | "warn" | "info" | "neutral";
  href: string;
  active: boolean;
  label: string;
  help: string;
}) {
  const toneCls: Record<typeof tone, string> = {
    ok: "border-emerald-400/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20",
    warn: "border-sun/40 bg-sun/10 text-sun hover:bg-sun/20",
    info: "border-sky-400/30 bg-sky-500/10 text-sky-300 hover:bg-sky-500/20",
    neutral: "border-white/15 bg-white/[0.04] text-white/70 hover:bg-white/[0.08]",
  };
  return (
    <a
      href={href}
      title={help}
      className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold transition ${toneCls[tone]} ${
        active ? "ring-2 ring-aqua/40" : ""
      }`}
    >
      {label}
    </a>
  );
}

function TargetChip({
  rule,
  groups,
}: {
  rule: InboxRoutingRule;
  groups: ApiInboxGroup[];
}) {
  if (rule.target_type === "user") {
    return <Badge tone="workspace">user</Badge>;
  }
  if (rule.target_type === "group") {
    const g = groups.find((x) => x.id === rule.target_group_id);
    return (
      <Badge tone="project">
        group · {g?.key ?? rule.target_group_id?.slice(0, 8) ?? "?"}
      </Badge>
    );
  }
  return <Badge tone="info">strategy · {rule.target_strategy ?? "?"}</Badge>;
}

function SelectedRuleEditor({
  workspace,
  rule,
  members,
  groups,
}: {
  workspace: ApiWorkspace;
  rule: InboxRoutingRuleDetail;
  members: ApiMember[];
  groups: ApiInboxGroup[];
}) {
  return (
    <div>
      <div className="flex items-start justify-between gap-3 px-5 pt-5">
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-2">
            <code className="text-[11px] font-bold text-aqua">{rule.handle}</code>
            {!rule.is_enabled && <Badge tone="warn">disabled</Badge>}
          </div>
          <h3 className="truncate font-display text-base font-bold text-white">
            {rule.target_type === "user"
              ? (rule.target_user_email ?? "Unknown user")
              : rule.target_type === "group"
                ? `${rule.target_group_name ?? rule.target_group_key ?? "Unknown group"}`
                : `Built-in: ${rule.target_strategy ?? "?"}`}
          </h3>
          <p className="mt-1 text-[11px] text-white/45">
            Created {new Date(rule.created_at).toLocaleDateString()} · updated{" "}
            {new Date(rule.updated_at).toLocaleDateString()}
          </p>
        </div>
        <form action="/api/inbox-routing/delete" method="POST">
          <input
            type="hidden"
            name="ws"
            value={workspace.id}
            suppressHydrationWarning
          />
          <input
            type="hidden"
            name="rule_id"
            value={rule.id}
            suppressHydrationWarning
          />
          <button
            type="submit"
            className="rounded-full border border-coral/40 bg-coral/10 px-3 py-1 text-[11px] font-semibold text-coral transition hover:border-coral/70 hover:bg-coral/20"
            title="Delete this rule. The handle then falls back to the workspace-admin chain."
          >
            Delete rule
          </button>
        </form>
      </div>

      <div className="mt-4 border-t border-white/5 px-5 py-5">
        <RuleEditor
          workspaceId={workspace.id}
          members={members}
          groups={groups}
          rule={rule}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mock fallback (no API / no session)
// ---------------------------------------------------------------------------

function MockView({
  reason,
  errorCode,
}: {
  reason: string;
  errorCode: string | null;
}) {
  return (
    <AppShell kicker="access" title="Inbox routing">
      <MockBanner reason={reason} />
      {errorCode && (
        <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
          {errorMessage(errorCode)}
        </div>
      )}
      <Card>
        <CardHeader
          title="Sign in to manage inbox routing"
          subtitle="Routing rules dereference symbolic handles like 'secops' into concrete owners (a user, an operational group, or a built-in strategy). They are workspace-scoped and require an admin role to manage."
        />
        <p className="text-sm text-white/65">
          Once signed in, you can bind handles to targets, dry-run preview the
          resolver, and audit which catalog handles are still unbound. Pair
          this surface with{" "}
          <a
            className="text-aqua underline underline-offset-2"
            href="/settings/groups"
          >
            Settings → Operational groups
          </a>{" "}
          to set up the underlying owner pools.
        </p>
      </Card>
    </AppShell>
  );
}
