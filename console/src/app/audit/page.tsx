/**
 * Audit log page (RFC-0006 phase 2.5).
 *
 * Reads `/v1/workspaces/{id}/audit-log` for the operator's current
 * workspace and renders the cursor-paginated history of every
 * privileged mutation. Owner / admin only — the backend returns 403 to
 * everyone else, and we surface that gracefully via the mock fallback.
 *
 * Filters are stateless: change the `?action=` query string to narrow
 * the view, and `?before=<id>` to walk older pages. Both round-trips go
 * through the standard Next.js search-params plumbing so the page stays
 * a Server Component.
 */

import Link from "next/link";

import { AppShell } from "@/components/app-shell";
import {
  Badge,
  Card,
  CardHeader,
  LiveBanner,
  MockBanner,
} from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listAuditLog,
  listWorkspaces,
} from "@/lib/api/client";
import type { ApiAuditEntry, ApiAuditPage, ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import { workspaces as mockWorkspaces } from "@/lib/mock/cloud";

export const dynamic = "force-dynamic";

const ACTION_FILTERS = [
  { id: "", label: "All" },
  { id: "workspace", label: "Workspace" },
  { id: "member", label: "Members" },
  { id: "auth", label: "Auth & tokens" },
  { id: "integration", label: "Integrations" },
  { id: "artifact_repo", label: "Artifact repos" },
] as const;

const PAGE_SIZE = 50;

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      page: ApiAuditPage;
      action: string;
      before: number | null;
    }
  | { source: "mock"; reason: string };

async function load(action: string, before: number | null): Promise<Mode> {
  if (!isApiConfigured()) return { source: "mock", reason: "SHIP_API_URL not set" };
  const token = await getSessionToken();
  if (!token) return { source: "mock", reason: "Sign in to view audit history" };
  try {
    const ws = await listWorkspaces(token);
    if (ws.length === 0)
      return {
        source: "mock",
        reason: "Create a workspace first to see audit history",
      };
    const target = ws[0];
    const page = await listAuditLog(
      target.id,
      { limit: PAGE_SIZE, before, action: action || null },
      token,
    );
    return { source: "live", workspace: target, page, action, before };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      return { source: "mock", reason: "Session expired — sign in again" };
    if (err instanceof ApiHttpError && err.status === 403)
      return {
        source: "mock",
        reason: "Audit log is owner / admin only — ask your workspace admin",
      };
    if (err instanceof ApiUnavailableError)
      return { source: "mock", reason: "Backend unreachable" };
    return { source: "mock", reason: "Backend returned an error" };
  }
}

function actionTone(action: string): "ok" | "warn" | "err" | "info" {
  if (action.endsWith(".delete") || action.endsWith(".remove")) return "err";
  if (action.endsWith(".revoke")) return "warn";
  if (action.startsWith("auth.")) return "info";
  if (action.endsWith(".invite") || action.endsWith(".create")) return "ok";
  return "info";
}

function actorLabel(entry: ApiAuditEntry): string {
  const a = entry.actor;
  if (a.token_name) return `${a.user_email ?? "?"} · ${a.token_name}`;
  if (a.user_email) return a.user_email;
  return "system";
}

function previewPayload(payload: Record<string, unknown>): string {
  const keys = Object.keys(payload);
  if (keys.length === 0) return "—";
  const slice = keys.slice(0, 4).map((k) => {
    const v = payload[k];
    const s =
      typeof v === "string"
        ? v.length > 32
          ? `${v.slice(0, 32)}…`
          : v
        : JSON.stringify(v);
    return `${k}=${s}`;
  });
  const more = keys.length > 4 ? ` +${keys.length - 4}` : "";
  return slice.join(" · ") + more;
}

function buildPagerUrl(action: string, cursor: number | null): string {
  const params = new URLSearchParams();
  if (action) params.set("action", action);
  if (cursor !== null) params.set("before", String(cursor));
  const q = params.toString();
  return q ? `/audit?${q}` : "/audit";
}

export default async function AuditPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const action =
    typeof params.action === "string" && params.action.length > 0
      ? params.action
      : "";
  const beforeRaw = typeof params.before === "string" ? params.before : null;
  const before = beforeRaw && /^\d+$/.test(beforeRaw) ? Number(beforeRaw) : null;

  const data = await load(action, before);

  if (data.source === "mock") {
    return <MockView reason={data.reason} />;
  }

  const { workspace, page } = data;
  const items = page.items;
  const nextHref =
    page.next_cursor !== null ? buildPagerUrl(action, page.next_cursor) : null;

  return (
    <AppShell
      kicker={`${workspace.name} · history`}
      title="Audit log"
      actions={
        <Link
          href={buildPagerUrl(action, null)}
          className="rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-bold text-white/85 transition hover:bg-white/[0.08]"
        >
          Reset
        </Link>
      }
    >
      <LiveBanner workspace={workspace.slug} />

      <Card className="mb-5">
        <CardHeader
          title="Filter"
          subtitle="Narrow by action category. Cursor pagination preserves the chosen filter."
        />
        <div className="flex flex-wrap gap-2">
          {ACTION_FILTERS.map((opt) => {
            const active = (opt.id || "") === action;
            return (
              <Link
                key={opt.id || "all"}
                href={buildPagerUrl(opt.id, null)}
                className={
                  "rounded-full border px-3 py-1 text-xs font-semibold transition " +
                  (active
                    ? "border-aqua/50 bg-aqua/15 text-aqua"
                    : "border-white/15 bg-white/[0.04] text-white/75 hover:bg-white/[0.08]")
                }
              >
                {opt.label}
              </Link>
            );
          })}
        </div>
      </Card>

      <Card padded={false} className="overflow-hidden">
        <CardHeader
          className="px-5 pt-5"
          title={`History · ${items.length} row${items.length === 1 ? "" : "s"}`}
          subtitle="Newest first. Each row is one privileged mutation; the JSON payload is recorded verbatim."
        />
        {items.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-white/60">
            No audit entries match this filter yet.
          </div>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">When</th>
                <th className="px-4 py-2 text-left font-semibold">Action</th>
                <th className="px-4 py-2 text-left font-semibold">Actor</th>
                <th className="px-4 py-2 text-left font-semibold">Target</th>
                <th className="px-4 py-2 text-left font-semibold">Payload</th>
              </tr>
            </thead>
            <tbody>
              {items.map((entry) => (
                <tr key={entry.id} className="border-t border-white/5 align-top">
                  <td className="px-4 py-3 text-xs text-white/65">
                    {new Date(entry.created_at).toUTCString()}
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone={actionTone(entry.action)} dot>
                      {entry.action}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-xs text-white/85">
                    {actorLabel(entry)}
                  </td>
                  <td className="px-4 py-3 text-xs text-white/65">
                    {entry.target_kind ? (
                      <>
                        <span className="text-white/85">{entry.target_kind}</span>
                        {entry.target_id ? (
                          <>
                            <span className="opacity-50"> · </span>
                            <code className="font-mono text-[11px] text-white/65">
                              {entry.target_id.slice(0, 12)}
                              {entry.target_id.length > 12 ? "…" : ""}
                            </code>
                          </>
                        ) : null}
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <code className="block max-w-[40ch] truncate font-mono text-[11px] text-white/55">
                      {previewPayload(entry.payload)}
                    </code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="flex items-center justify-between border-t border-white/5 px-5 py-3">
          <span className="text-[11px] text-white/50">
            Page size {PAGE_SIZE}. Cursor is the smallest visible audit id.
          </span>
          {nextHref ? (
            <Link
              href={nextHref}
              className="rounded-full bg-aqua/80 px-4 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua"
            >
              Older →
            </Link>
          ) : (
            <span className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/45">
              End of history
            </span>
          )}
        </div>
      </Card>
    </AppShell>
  );
}

function MockView({ reason }: { reason: string }) {
  const ws = mockWorkspaces[0];
  const sample: ApiAuditEntry[] = [
    {
      id: 12,
      action: "member.invite",
      target_kind: "user",
      target_id: "00000000-0000-0000-0000-000000000abc",
      payload: { email: "asha@helio.dev", role: "maintainer", user_created: true },
      created_at: new Date(Date.now() - 1000 * 60 * 25).toISOString(),
      actor: {
        user_id: null,
        user_email: "denis@helio.dev",
        token_id: null,
        token_name: null,
      },
    },
    {
      id: 11,
      action: "auth.token.mint",
      target_kind: "api_token",
      target_id: "00000000-0000-0000-0000-000000000123",
      payload: { name: "ship-cli", scopes: ["workspace:read", "workspace:write"] },
      created_at: new Date(Date.now() - 1000 * 60 * 95).toISOString(),
      actor: {
        user_id: null,
        user_email: "denis@helio.dev",
        token_id: null,
        token_name: null,
      },
    },
    {
      id: 10,
      action: "workspace.update",
      target_kind: "workspace",
      target_id: ws.id,
      payload: { catalog_sources: { workspace: true, project: true, global: false } },
      created_at: new Date(Date.now() - 1000 * 60 * 60 * 5).toISOString(),
      actor: {
        user_id: null,
        user_email: "mira@helio.dev",
        token_id: null,
        token_name: null,
      },
    },
  ];

  return (
    <AppShell kicker={`${ws.name} · history`} title="Audit log">
      <MockBanner reason={reason} />
      <Card padded={false} className="overflow-hidden">
        <CardHeader
          className="px-5 pt-5"
          title="Recent mutations (sample)"
          subtitle="Sign in as a workspace owner or admin to see the real history."
        />
        <table className="min-w-full text-sm">
          <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
            <tr>
              <th className="px-4 py-2 text-left font-semibold">When</th>
              <th className="px-4 py-2 text-left font-semibold">Action</th>
              <th className="px-4 py-2 text-left font-semibold">Actor</th>
              <th className="px-4 py-2 text-left font-semibold">Payload</th>
            </tr>
          </thead>
          <tbody>
            {sample.map((entry) => (
              <tr key={entry.id} className="border-t border-white/5 align-top">
                <td className="px-4 py-3 text-xs text-white/65">
                  {new Date(entry.created_at).toUTCString()}
                </td>
                <td className="px-4 py-3">
                  <Badge tone={actionTone(entry.action)} dot>
                    {entry.action}
                  </Badge>
                </td>
                <td className="px-4 py-3 text-xs text-white/85">
                  {actorLabel(entry)}
                </td>
                <td className="px-4 py-3">
                  <code className="block max-w-[40ch] truncate font-mono text-[11px] text-white/55">
                    {previewPayload(entry.payload)}
                  </code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </AppShell>
  );
}
