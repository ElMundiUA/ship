/**
 * Operational groups settings page (RFC-0010 ticket P2-15).
 *
 * Workspace-scoped CRUD over `member_groups` + `member_group_members`.
 * Operational groups (`secops`, `eng_managers`, `on_call_eng`, …) are
 * intentionally distinct from `WorkspaceMember.role`: roles answer
 * "what permissions do you have?", groups answer "who handles X?".
 *
 * The Inbox routing rules (settings/inbox-routing, P2-16) consume
 * these groups when a rule has `target_type=group`. Group strategy
 * (`round_robin` / `oncall` / `first`) decides which member catches
 * the next item.
 *
 * Layout:
 *   - Left panel: list of groups + create form
 *   - Right panel: detail of the currently-selected group + member
 *     management
 *
 * Server component, no JS required: every mutation is a tiny
 * <form action="/api/inbox-groups/..."> POST that 303-redirects back
 * here. Falls back to a static MockView when the API isn't
 * configured / no session — matches the convention used by /members.
 */

import { AppShell } from "@/components/app-shell";
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
  getInboxGroup,
  getMe,
  isApiConfigured,
  listInboxGroups,
  listMembers,
  listWorkspaces,
} from "@/lib/api/client";
import type {
  ApiInboxGroup,
  ApiInboxGroupDetail,
} from "@/lib/api/client";
import type {
  ApiMember,
  ApiUser,
  ApiWorkspace,
} from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";

export const dynamic = "force-dynamic";

const STRATEGY_LABEL: Record<ApiInboxGroup["assignment_strategy"], string> = {
  round_robin: "Round-robin",
  oncall: "On-call first",
  first: "First member",
};

const STRATEGY_HELP: Record<ApiInboxGroup["assignment_strategy"], string> = {
  round_robin:
    "Each new item rotates to the next member by added-at order. Even load.",
  oncall:
    "Items go to the on-call member; falls back to first by added-at when no one is on-call.",
  first:
    "All items go to the oldest member. Useful for solo escalation paths.",
};

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      groups: ApiInboxGroup[];
      selected: ApiInboxGroupDetail | null;
      members: ApiMember[];
      me: ApiUser | null;
    }
  | { source: "mock"; reason: string };

function errorMessage(code: string): string {
  switch (code) {
    case "bad_input":
      return "Missing required fields. Group key + display name are both required.";
    case "bad_key":
      return "Group key must be lowercase, start with a letter, and use only letters / digits / underscores.";
    case "duplicate_key":
      return "A group with that key already exists in this workspace.";
    case "duplicate":
      return "That user is already in the group.";
    case "not_workspace_member":
      return "Only existing workspace members can be added to a group. Invite them under /members first.";
    case "forbidden":
      return "Admin role required to manage operational groups.";
    case "not_found":
      return "Group not found — it may have been deleted in another tab.";
    case "api_unavailable":
      return "Backend is unreachable. Try again in a moment.";
    case "invalid_input":
      return "The backend rejected the input — check the field values.";
    default:
      return `Couldn't apply the change (${code}). Try again or refresh.`;
  }
}

async function load(selectedGroupId: string | null): Promise<Mode> {
  if (!isApiConfigured())
    return { source: "mock", reason: "SHIP_API_URL is not set" };
  const token = await getSessionToken();
  if (!token)
    return { source: "mock", reason: "Sign in to manage real groups" };
  try {
    const ws = await listWorkspaces(token);
    if (ws.length === 0)
      return {
        source: "mock",
        reason: "Create a workspace first to manage groups",
      };
    const workspace = ws[0];
    const [groups, members, me] = await Promise.all([
      listInboxGroups(workspace.id, token),
      listMembers(workspace.id, token),
      getMe(token).catch(() => null as ApiUser | null),
    ]);

    let selected: ApiInboxGroupDetail | null = null;
    const target = selectedGroupId
      ? groups.find((g) => g.id === selectedGroupId)
      : groups[0];
    if (target) {
      try {
        selected = await getInboxGroup(workspace.id, target.id, token);
      } catch {
        selected = null;
      }
    }

    return { source: "live", workspace, groups, selected, members, me };
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

export default async function GroupsSettingsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const selectedGroupId =
    typeof params.group === "string" ? params.group : null;
  const errorCode = typeof params.error === "string" ? params.error : null;
  const justDeleted = params.deleted === "1";

  const data = await load(selectedGroupId);

  if (data.source === "mock") {
    return <MockView reason={data.reason} errorCode={errorCode} />;
  }

  const { workspace, groups, selected, members, me } = data;

  return (
    <AppShell
      kicker="access"
      title="Operational groups"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      me={meToShellUser(me)}
    >
      <LiveBanner workspace={workspace.slug} />

      {errorCode && (
        <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
          {errorMessage(errorCode)}
        </div>
      )}
      {justDeleted && !errorCode && (
        <div className="mb-5 rounded-xl border border-emerald-400/30 bg-emerald-500/[0.06] px-3 py-2 text-xs text-emerald-300">
          Group deleted. Any inbox routing rules that pointed at it are now
          orphaned — review them under Settings → Inbox routing.
        </div>
      )}

      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Stat label="Groups" value={groups.length.toString()} />
        <Stat
          label="Total assignments"
          value={groups
            .reduce((acc, g) => acc + g.member_count, 0)
            .toString()}
          hint="Sum of members across every group; users can belong to many."
        />
        <Stat
          label="Workspace members"
          value={members.length.toString()}
          hint="Pool of users that can be added to a group."
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_1.4fr]">
        <Card padded={false} className="overflow-hidden">
          <CardHeader
            className="px-5 pt-5"
            title="Groups"
            subtitle="Symbolic buckets the inbox routing rules dereference into owners."
          />
          {groups.length === 0 ? (
            <div className="px-5 pb-5">
              <EmptyState
                title="No groups yet"
                body="Operational groups are how the unified Inbox decides who handles each item. Pick a key (e.g. secops), add the responsible workspace members, and reference the group from a routing rule under Settings → Inbox routing."
              />
            </div>
          ) : (
            <ul className="divide-y divide-white/5">
              {groups.map((g) => {
                const active = selected?.id === g.id;
                return (
                  <li key={g.id}>
                    <a
                      href={`/settings/groups?group=${g.id}`}
                      className={`flex items-center justify-between gap-3 px-5 py-3 transition ${
                        active
                          ? "bg-aqua/10 border-l-2 border-aqua"
                          : "hover:bg-white/[0.04]"
                      }`}
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <code className="text-[11px] font-bold text-aqua">
                            {g.key}
                          </code>
                          <Badge tone="neutral">{STRATEGY_LABEL[g.assignment_strategy]}</Badge>
                        </div>
                        <div className="truncate text-sm font-semibold text-white">
                          {g.name}
                        </div>
                        {g.description && (
                          <div className="truncate text-[11px] text-white/55">
                            {g.description}
                          </div>
                        )}
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="text-xs font-bold text-white/80">
                          {g.member_count}
                        </div>
                        <div className="text-[10px] uppercase tracking-wider text-white/40">
                          members
                        </div>
                      </div>
                    </a>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="border-t border-white/5 bg-white/[0.02] px-5 py-4">
            <h4 className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/55">
              New group
            </h4>
            <form
              action="/api/inbox-groups/create"
              method="POST"
              className="grid grid-cols-1 gap-2"
            >
              <input
                type="hidden"
                name="ws"
                value={workspace.id}
                suppressHydrationWarning
              />
              <input
                name="key"
                placeholder="key (e.g. secops)"
                required
                pattern="^[a-z][a-z0-9_]*$"
                title="lowercase, starts with a letter, only letters/digits/underscores"
                suppressHydrationWarning
                className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
              />
              <input
                name="name"
                placeholder="Display name (e.g. Security & ops)"
                required
                suppressHydrationWarning
                className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
              />
              <input
                name="description"
                placeholder="Optional one-line description"
                suppressHydrationWarning
                className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white/85 outline-none focus:border-aqua/40"
              />
              <select
                name="strategy"
                defaultValue="round_robin"
                suppressHydrationWarning
                className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-xs text-white outline-none focus:border-aqua/40"
              >
                <option value="round_robin">Round-robin (rotate)</option>
                <option value="oncall">On-call first</option>
                <option value="first">First member only</option>
              </select>
              <button
                type="submit"
                className="mt-1 rounded-full bg-aqua/80 px-4 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua"
              >
                Create group
              </button>
            </form>
          </div>
        </Card>

        <GroupDetailPanel
          workspace={workspace}
          group={selected}
          members={members}
        />
      </div>
    </AppShell>
  );
}

function GroupDetailPanel({
  workspace,
  group,
  members,
}: {
  workspace: ApiWorkspace;
  group: ApiInboxGroupDetail | null;
  members: ApiMember[];
}) {
  if (!group) {
    return (
      <Card>
        <CardHeader
          title="Pick a group on the left"
          subtitle="Or create one to start routing inbox items to a team."
        />
        <p className="text-sm text-white/65">
          Operational groups are referenced by inbox routing rules under{" "}
          <a
            className="text-aqua underline underline-offset-2"
            href="/settings/inbox-routing"
          >
            Settings → Inbox routing
          </a>
          . Each rule maps a symbolic handle (like <code>secops</code> or{" "}
          <code>repo_maintainer</code>) to either a single user, a group, or
          a built-in strategy.
        </p>
      </Card>
    );
  }

  const inGroup = new Set(group.members.map((m) => m.user_id));
  const candidates = members.filter((m) => !inGroup.has(m.user_id));

  return (
    <Card padded={false} className="overflow-hidden">
      <div className="flex items-start justify-between gap-3 px-5 pt-5">
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-2">
            <code className="text-[11px] font-bold text-aqua">{group.key}</code>
            <Badge tone="neutral">{STRATEGY_LABEL[group.assignment_strategy]}</Badge>
          </div>
          <h3 className="truncate font-display text-base font-bold text-white">
            {group.name}
          </h3>
          {group.description && (
            <p className="mt-0.5 text-xs text-white/55">{group.description}</p>
          )}
          <p className="mt-2 text-[11px] text-white/45">
            {STRATEGY_HELP[group.assignment_strategy]}
          </p>
        </div>
        <form action="/api/inbox-groups/delete" method="POST">
          <input
            type="hidden"
            name="ws"
            value={workspace.id}
            suppressHydrationWarning
          />
          <input
            type="hidden"
            name="group_id"
            value={group.id}
            suppressHydrationWarning
          />
          <button
            type="submit"
            className="rounded-full border border-coral/40 bg-coral/10 px-3 py-1 text-[11px] font-semibold text-coral transition hover:border-coral/70 hover:bg-coral/20"
            title="Removing a group orphans any routing rule that pointed at it; review under Inbox routing."
          >
            Delete group
          </button>
        </form>
      </div>

      <div className="mt-4 border-t border-white/5">
        <CardHeader
          className="px-5 pt-4"
          title={`Members (${group.members.length})`}
          subtitle={
            group.assignment_strategy === "oncall"
              ? "Toggle on-call to make a member the primary catcher; the rest serve as fallbacks."
              : "Order shown by added-at; round-robin walks members in this order."
          }
        />
        {group.members.length === 0 ? (
          <div className="px-5 pb-5">
            <EmptyState
              title="No members yet"
              body="Pick a workspace member from the dropdown below to add them. Until then, every routing rule pointing at this group falls through to the workspace-admin fallback."
            />
          </div>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Member</th>
                <th className="px-4 py-2 text-left font-semibold">Added</th>
                <th className="px-4 py-2 text-left font-semibold">On-call</th>
                <th className="px-4 py-2 text-right font-semibold"></th>
              </tr>
            </thead>
            <tbody>
              {group.members.map((m) => (
                <tr key={m.user_id} className="border-t border-white/5">
                  <td className="px-4 py-2.5">
                    <div className="text-sm font-semibold text-white">
                      {m.display_name?.trim() || m.email}
                    </div>
                    {m.display_name && (
                      <div className="text-[11px] text-white/45">{m.email}</div>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-white/55">
                    {new Date(m.added_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-2.5">
                    {m.on_call ? (
                      <Badge tone="ok" dot>
                        on-call
                      </Badge>
                    ) : (
                      <span className="text-[11px] text-white/35">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <form
                      action="/api/inbox-groups/members"
                      method="POST"
                      className="inline-flex"
                    >
                      <input
                        type="hidden"
                        name="ws"
                        value={workspace.id}
                        suppressHydrationWarning
                      />
                      <input
                        type="hidden"
                        name="group_id"
                        value={group.id}
                        suppressHydrationWarning
                      />
                      <input type="hidden" name="op" value="remove" />
                      <input
                        type="hidden"
                        name="user_id"
                        value={m.user_id}
                        suppressHydrationWarning
                      />
                      <button
                        type="submit"
                        className="rounded-full border border-white/15 bg-white/[0.04] px-2.5 py-0.5 text-[10px] font-semibold text-white/70 transition hover:border-coral/40 hover:bg-coral/10 hover:text-coral"
                      >
                        Remove
                      </button>
                    </form>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="border-t border-white/5 bg-white/[0.02] px-5 py-4">
          <h4 className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/55">
            Add a workspace member
          </h4>
          {candidates.length === 0 ? (
            <p className="text-xs text-white/55">
              All workspace members are already in this group.
            </p>
          ) : (
            <form
              action="/api/inbox-groups/members"
              method="POST"
              className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-end gap-2"
            >
              <input
                type="hidden"
                name="ws"
                value={workspace.id}
                suppressHydrationWarning
              />
              <input
                type="hidden"
                name="group_id"
                value={group.id}
                suppressHydrationWarning
              />
              <input type="hidden" name="op" value="add" />
              <label className="block">
                <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
                  Member
                </span>
                <select
                  name="user_id"
                  defaultValue={candidates[0]?.user_id}
                  required
                  suppressHydrationWarning
                  className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
                >
                  {candidates.map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.display_name?.trim() || m.email}
                    </option>
                  ))}
                </select>
              </label>
              {group.assignment_strategy === "oncall" && (
                <label className="flex items-end gap-2 text-xs text-white/75">
                  <input
                    type="checkbox"
                    name="on_call"
                    value="1"
                    suppressHydrationWarning
                    className="h-4 w-4 rounded border-white/20 bg-white/10"
                  />
                  on-call
                </label>
              )}
              <button
                type="submit"
                className="rounded-full bg-aqua/80 px-4 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua"
              >
                Add
              </button>
            </form>
          )}
        </div>
      </div>
    </Card>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <Card>
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </div>
      <div className="mt-1 font-display text-2xl font-bold text-white">
        {value}
      </div>
      {hint && <div className="mt-1 text-[10px] text-white/45">{hint}</div>}
    </Card>
  );
}

function MockView({
  reason,
  errorCode,
}: {
  reason: string;
  errorCode: string | null;
}) {
  return (
    <AppShell kicker="access" title="Operational groups">
      <MockBanner reason={reason} />
      {errorCode && (
        <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
          {errorMessage(errorCode)}
        </div>
      )}
      <Card>
        <CardHeader
          title="Sign in to manage operational groups"
          subtitle="Operational groups (secops, eng_managers, on_call_eng, …) are how the unified Inbox decides who handles each item. They are workspace-scoped and require an admin role to manage."
        />
        <p className="text-sm text-white/65">
          Once signed in, you can create groups, add workspace members, and
          pick an assignment strategy (round-robin, on-call, or first). The
          inbox routing rules under{" "}
          <a
            className="text-aqua underline underline-offset-2"
            href="/settings/inbox-routing"
          >
            Settings → Inbox routing
          </a>{" "}
          dereference symbolic handles into one of these groups.
        </p>
      </Card>
    </AppShell>
  );
}
