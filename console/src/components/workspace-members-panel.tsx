/**
 * Workspace members roster (used inside Workspace settings).
 *
 * Mutations go through <form action="/api/members/..."> POSTs.
 */

import {
  Badge,
  ButtonGhost,
  Card,
  CardHeader,
} from "@/components/ui";
import { MemberAccessModal } from "@/components/member-access-modal";
import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listInvites,
  listMembers,
  listWorkspaces,
  type ApiInvite,
} from "@/lib/api/client";
import { consumeInviteTokens } from "@/lib/api/invite-stash";
import { MEMBER_ROLES } from "@/lib/member-access";
import type { ApiMember, ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import { pickWorkspace } from "@/lib/workspace-scope";

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      allWorkspaces: ApiWorkspace[];
      members: ApiMember[];
      invites: ApiInvite[];
    }
  | { source: "mock"; reason: string };

function errorMessage(code: string): string {
  switch (code) {
    case "bad_input":
      return "Missing required fields. Email and role are both required.";
    case "invalid_email":
      return "That email looks malformed. Use a valid mailbox like name@company.com.";
    case "duplicate":
      return "This person already has that role on the workspace.";
    case "forbidden":
      return "You need admin or owner role to manage members.";
    case "not_found":
      return "That member is gone — they may have been removed in another tab.";
    case "last_owner":
      return "This is the last workspace owner. Promote another member to owner first.";
    case "api_unavailable":
      return "Backend is unreachable. Try again in a moment.";
    default:
      return `Couldn't apply the change (${code}). Try again or refresh.`;
  }
}

export async function loadMembersWorkspaceMode(
  wsParam: string | undefined,
): Promise<Mode> {
  if (!isApiConfigured()) return { source: "mock", reason: "SHIP_API_URL not set" };
  const token = await getSessionToken();
  if (!token) return { source: "mock", reason: "Sign in to manage real members" };
  try {
    const ws = await listWorkspaces(token);
    if (ws.length === 0)
      return {
        source: "mock",
        reason: "Create a workspace first to manage members",
      };
    const target = pickWorkspace(ws, wsParam);
    const [members, invites] = await Promise.all([
      listMembers(target.id, token),
      listInvites(target.id, token).catch(() => [] as ApiInvite[]),
    ]);
    return {
      source: "live",
      workspace: target,
      allWorkspaces: ws,
      members,
      invites,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      return { source: "mock", reason: "Session expired — sign in again" };
    if (err instanceof ApiUnavailableError)
      return { source: "mock", reason: "Backend unreachable" };
    return { source: "mock", reason: "Backend returned an error" };
  }
}

function initialsFor(member: ApiMember): string {
  const source = member.display_name?.trim() || member.email;
  return initialsFromSource(source);
}

function initialsFromSource(source: string): string {
  const parts = source.split(/[\s@.]+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return (parts[0]?.slice(0, 2) ?? "??").toUpperCase();
}

export type LiveMembersMode = Extract<Mode, { source: "live" }>;

export async function WorkspaceMembersPanelLoader({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const { parseWorkspaceIdParam } = await import("@/lib/workspace-scope");
  const wsParam = parseWorkspaceIdParam(params.ws);
  const data = await loadMembersWorkspaceMode(wsParam);
  const errorCode = typeof params.error === "string" ? params.error : null;
  if (data.source === "mock") {
    return (
      <p className="text-sm text-white/55">
        Members aren&apos;t available: {data.reason}
      </p>
    );
  }
  const freshTokens = await consumeInviteTokens();
  const invitedCount = (() => {
    const raw = params.invited;
    const v = typeof raw === "string" ? raw : Array.isArray(raw) ? raw[0] : undefined;
    const n = v ? Number.parseInt(v, 10) : NaN;
    return Number.isFinite(n) ? n : null;
  })();
  const inviteErrorCode = typeof params.invite_error === "string" ? params.invite_error : null;
  const wasRevoked = params.revoked === "1";
  const resentStatus = typeof params.resent === "string" ? params.resent : null;
  return (
    <WorkspaceMembersPanelContent
      data={data}
      errorCode={errorCode}
      freshTokens={freshTokens}
      invitedCount={invitedCount}
      inviteErrorCode={inviteErrorCode}
      wasRevoked={wasRevoked}
      resentStatus={resentStatus}
    />
  );
}

export function WorkspaceMembersPanelContent({
  data,
  errorCode,
  freshTokens,
  invitedCount,
  inviteErrorCode,
  wasRevoked,
  resentStatus,
}: {
  data: LiveMembersMode;
  errorCode: string | null;
  freshTokens: Record<string, string>;
  invitedCount: number | null;
  inviteErrorCode: string | null;
  wasRevoked: boolean;
  resentStatus: string | null;
}) {
  const { workspace, members, invites } = data;
  const owners = members.filter((m) => m.role === "owner");
  const admins = members.filter((m) => m.role === "admin");
  const pending = members.filter((m) => m.pending);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <ButtonGhost type="button">Open invite link</ButtonGhost>
        <a
          href="#settings-invite"
          className="rounded-full bg-aqua/80 px-3 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua"
        >
          + Invite
        </a>
      </div>
      {errorCode && (
        <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
          {errorMessage(errorCode)}
        </div>
      )}

      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Total" value={members.length.toString()} />
        <Stat
          label="Owners + admins"
          value={(owners.length + admins.length).toString()}
        />
        <Stat label="Pending invites" value={pending.length.toString()} />
        <Stat
          label="Owners"
          value={owners.length.toString()}
          tone={owners.length === 1 ? "warn" : "ok"}
          hint={
            owners.length === 1
              ? "Only one owner — promote a backup before going on holiday"
              : "Healthy: more than one owner"
          }
        />
      </div>

      <Card padded={false} className="overflow-hidden">
        <CardHeader
          className="px-5 pt-5"
          title="Workspace members"
          subtitle="Workspace role and which specialist Inbox lanes each person can cover — use Edit access to change."
        />
        <table className="min-w-full text-sm">
          <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
            <tr>
              <th className="px-4 py-2 text-left font-semibold">Member</th>
              <th className="px-4 py-2 text-left font-semibold">Access</th>
              <th className="px-4 py-2 text-left font-semibold">Status</th>
              <th className="px-4 py-2 text-left font-semibold">Added</th>
              <th className="px-4 py-2 text-right font-semibold"></th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <MemberRow key={m.id} member={m} workspaceId={workspace.id} />
            ))}
          </tbody>
        </table>
      </Card>

      <TeamInvitesSection
        workspaceId={workspace.id}
        invites={invites}
        freshTokens={freshTokens}
        invitedCount={invitedCount}
        inviteErrorCode={inviteErrorCode}
        wasRevoked={wasRevoked}
        resentStatus={resentStatus}
      />

      <Card className="mt-6" id="settings-invite">
        <CardHeader
          title="Invite teammate (legacy / single)"
          subtitle="Immediate Auth0-backed pending user — prefer the bulk team invite block above."
        />
        <form
          action="/api/members/invite"
          method="POST"
          className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_10rem_auto]"
        >
          <input
            type="hidden"
            name="ws"
            value={workspace.id}
            suppressHydrationWarning
          />
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
              Email
            </span>
            <input
              name="email"
              type="email"
              required
              placeholder="teammate@company.com"
              suppressHydrationWarning
              className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
              Role
            </span>
            <select
              name="role"
              defaultValue="member"
              suppressHydrationWarning
              className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
            >
              {MEMBER_ROLES.filter((r) => r !== "owner").map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
              <option value="owner">owner</option>
            </select>
          </label>
          <div className="flex items-end">
            <button
              type="submit"
              className="w-full rounded-full bg-aqua/80 px-4 py-1.5 text-sm font-bold text-ink transition hover:bg-aqua md:w-auto"
            >
              Invite
            </button>
          </div>
        </form>
        <p className="mt-3 text-[11px] text-white/55">
          Legacy path: pre-creates the row but does not email anyone. Use the
          team-invites form above for the full flow (token + welcome email).
          When they eventually sign in with this email, Ship binds their IdP
          subject to the row above and the &ldquo;pending&rdquo; badge flips off.
        </p>
      </Card>
    </div>
  );
}

function MemberRow({
  member,
  workspaceId,
}: {
  member: ApiMember;
  workspaceId: string;
}) {
  return (
    <tr className="border-t border-white/5 hover:bg-white/[0.02]">
      <td className="px-4 py-3 align-top">
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-aqua via-lilac to-coral text-[11px] font-bold text-ink">
            {initialsFor(member)}
          </span>
          <div className="min-w-0">
            <div className="font-semibold text-white">
              {member.display_name || member.email}
            </div>
            <div className="text-[11px] text-white/50">{member.email}</div>
          </div>
        </div>
      </td>
      <td className="px-4 py-3 align-top">
        <MemberAccessModal member={member} workspaceId={workspaceId} />
      </td>
      <td className="px-4 py-3 align-top">
        {member.pending ? (
          <Badge tone="warn" dot>
            pending
          </Badge>
        ) : (
          <Badge tone="ok" dot>
            active
          </Badge>
        )}
      </td>
      <td className="px-4 py-3 align-top text-xs text-white/55">
        {new Date(member.created_at).toUTCString()}
      </td>
      <td className="px-4 py-3 text-right align-top">
        <form action="/api/members/remove" method="POST" className="inline-block">
          <input
            type="hidden"
            name="ws"
            value={workspaceId}
            suppressHydrationWarning
          />
          <input
            type="hidden"
            name="member"
            value={member.id}
            suppressHydrationWarning
          />
          <button
            type="submit"
            className="text-[11px] font-semibold text-coral/80 hover:text-coral"
          >
            Remove
          </button>
        </form>
      </td>
    </tr>
  );
}

function Stat({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn";
  hint?: string;
}) {
  return (
    <Card>
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <div className="font-display text-2xl font-bold text-white">{value}</div>
        {tone && (
          <span
            className={
              "text-[10px] font-semibold " +
              (tone === "warn" ? "text-coral/80" : "text-aqua/80")
            }
          >
            {tone === "warn" ? "fragile" : "ok"}
          </span>
        )}
      </div>
      {hint && <div className="mt-1 text-[10px] text-white/45">{hint}</div>}
    </Card>
  );
}

function TeamInvitesSection({
  workspaceId,
  invites,
  freshTokens,
  invitedCount,
  inviteErrorCode,
  wasRevoked,
  resentStatus,
}: {
  workspaceId: string;
  invites: ApiInvite[];
  freshTokens: Record<string, string>;
  invitedCount: number | null;
  inviteErrorCode: string | null;
  wasRevoked: boolean;
  resentStatus: string | null;
}) {
  const pending = invites.filter((i) => !i.accepted_at && !i.revoked_at);
  const history = invites
    .filter((i) => i.accepted_at || i.revoked_at)
    .slice(0, 5);
  const freshIds = new Set(Object.keys(freshTokens));
  return (
    <Card className="mt-6" id="team-invites">
      <CardHeader
        title="Team invites"
        subtitle="Bulk-paste emails — Ship sends each invitee a welcome email and keeps the accept URL handy as a copy-link fallback."
      />

      {invitedCount !== null && (
        <div className="mb-4 rounded-xl border border-aqua/30 bg-aqua/10 px-3 py-2 text-xs text-white/85">
          {invitedCount === 1
            ? "Invite minted and emailed. Forward the accept URL below if the email gets stuck."
            : `${invitedCount} invites minted and emailed. Forward the accept URLs below if any email gets stuck.`}
        </div>
      )}
      {wasRevoked && (
        <div className="mb-4 rounded-xl border border-white/20 bg-white/[0.04] px-3 py-2 text-xs text-white/75">
          Invite revoked. Issue a fresh one whenever they&rsquo;re ready.
        </div>
      )}
      {resentStatus && (
        <div className="mb-4 rounded-xl border border-aqua/30 bg-aqua/10 px-3 py-2 text-xs text-white/85">
          {resentStatus === "skipped"
            ? "Invite re-issued. Email transport is disabled (EMAIL_PROVIDER=none); copy the new accept URL below."
            : "Invite re-issued and emailed. The previous accept URL stops working immediately."}
        </div>
      )}
      {inviteErrorCode && (
        <div className="mb-4 rounded-xl border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-white/85">
          {inviteErrorLabel(inviteErrorCode)}
        </div>
      )}

      <form
        action="/api/team/create-invites"
        method="POST"
        className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_10rem_6rem_auto]"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Emails (comma, space or newline separated)
          </span>
          <textarea
            name="emails"
            rows={3}
            required
            placeholder={"alice@acme.dev\nbob@acme.dev\ncharlie@acme.dev"}
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 font-mono text-xs text-white outline-none focus:border-aqua/40"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            Role
          </span>
          <select
            name="role"
            defaultValue="member"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
          >
            <option value="admin">admin</option>
            <option value="maintainer">maintainer</option>
            <option value="member">member</option>
            <option value="viewer">viewer</option>
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
            TTL days
          </span>
          <input
            type="number"
            name="ttl_days"
            min={1}
            max={60}
            defaultValue={7}
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
          />
        </label>
        <div className="flex items-end">
          <button
            type="submit"
            className="w-full rounded-full bg-aqua/80 px-4 py-1.5 text-sm font-bold text-ink transition hover:bg-aqua md:w-auto"
          >
            Send invites
          </button>
        </div>
      </form>

      {pending.length > 0 && (
        <div className="mt-5 overflow-x-auto">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/45">
            Pending ({pending.length})
          </div>
          <table className="min-w-full text-xs">
            <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
              <tr>
                <th className="px-3 py-2 text-left font-semibold">Email</th>
                <th className="px-3 py-2 text-left font-semibold">Role</th>
                <th className="px-3 py-2 text-left font-semibold">Email</th>
                <th className="px-3 py-2 text-left font-semibold">Expires</th>
                <th className="px-3 py-2 text-left font-semibold">
                  Accept URL
                </th>
                <th className="px-3 py-2 text-right font-semibold"></th>
              </tr>
            </thead>
            <tbody>
              {pending.map((invite) => {
                const acceptUrl = freshTokens[invite.id];
                return (
                  <tr
                    key={invite.id}
                    className="border-t border-white/5 hover:bg-white/[0.02]"
                  >
                    <td className="px-3 py-2 text-white">{invite.email}</td>
                    <td className="px-3 py-2 text-white/75">{invite.role}</td>
                    <td className="px-3 py-2">
                      <EmailStatusBadge status={invite.email_status} />
                    </td>
                    <td className="px-3 py-2 text-white/55">
                      {new Date(invite.expires_at).toLocaleDateString()}
                    </td>
                    <td className="px-3 py-2">
                      {acceptUrl ? (
                        <code className="break-all rounded bg-white/[0.07] px-2 py-1 text-[11px] text-aqua">
                          {acceptUrl}
                        </code>
                      ) : freshIds.has(invite.id) ? (
                        <span className="text-[11px] text-white/50">
                          (session expired — resend to mint a new link)
                        </span>
                      ) : (
                        <span className="text-[11px] text-white/50">
                          —{" "}
                          <span className="italic">
                            (resend to mint a fresh URL)
                          </span>
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="inline-flex items-center gap-3">
                        <form
                          action="/api/team/resend-invite"
                          method="POST"
                          className="inline-block"
                        >
                          <input
                            type="hidden"
                            name="ws"
                            value={workspaceId}
                          />
                          <input
                            type="hidden"
                            name="invite_id"
                            value={invite.id}
                          />
                          <button
                            type="submit"
                            className="text-[11px] font-semibold text-aqua/80 hover:text-aqua"
                          >
                            Resend
                          </button>
                        </form>
                        <form
                          action="/api/team/revoke-invite"
                          method="POST"
                          className="inline-block"
                        >
                          <input
                            type="hidden"
                            name="ws"
                            value={workspaceId}
                          />
                          <input
                            type="hidden"
                            name="invite_id"
                            value={invite.id}
                          />
                          <button
                            type="submit"
                            className="text-[11px] font-semibold text-coral/80 hover:text-coral"
                          >
                            Revoke
                          </button>
                        </form>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {history.length > 0 && (
        <div className="mt-5 text-[11px] text-white/55">
          <div className="mb-1 font-semibold uppercase tracking-widest text-white/45">
            Recent
          </div>
          <ul className="space-y-1">
            {history.map((invite) => (
              <li key={invite.id}>
                {invite.email} ·{" "}
                {invite.accepted_at
                  ? `accepted ${new Date(invite.accepted_at).toLocaleDateString()}`
                  : `revoked ${new Date(invite.revoked_at as string).toLocaleDateString()}`}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function EmailStatusBadge({
  status,
}: {
  status: ApiInvite["email_status"];
}) {
  // ``null`` covers two distinct cases: pre-email-feature rows and
  // the GET /invites endpoint, which never returns an email status
  // because nothing is sent on a list call. Render a neutral
  // placeholder so the column reads well without leaking
  // implementation detail to the operator.
  if (status === null || status === undefined) {
    return <span className="text-[11px] text-white/35">—</span>;
  }
  if (status === "queued") {
    return (
      <Badge tone="ok" dot>
        sent
      </Badge>
    );
  }
  if (status === "skipped") {
    return (
      <Badge tone="warn" dot>
        skipped
      </Badge>
    );
  }
  return (
    <Badge tone="warn" dot>
      disabled
    </Badge>
  );
}

function inviteErrorLabel(code: string): string {
  switch (code) {
    case "forbidden":
      return "You need admin to create or revoke invites.";
    case "bad_input":
      return "Paste at least one email and pick a role.";
    case "empty":
      return "Email list is empty — paste one or more emails.";
    case "not_found":
      return "Invite already gone — refresh the page.";
    case "already_accepted":
      return "That invite was already accepted — remove the member instead.";
    case "api_unavailable":
      return "Backend is unreachable. Try again.";
    default:
      return `Couldn't apply the change (${code}). Try again or refresh.`;
  }
}
