/**
 * Audit log page (RFC-0006 phase 2.5 + D12 filter depth).
 *
 * Reads `/v1/workspaces/{id}/audit-log` for the operator's current
 * workspace and renders the cursor-paginated history of every
 * privileged mutation. Owner / admin only — the backend returns 403 to
 * everyone else, and we surface that gracefully via the mock fallback.
 *
 * **D12 extensions (2026-04-20).** The filter form now covers the full
 * compliance surface the plan promised:
 *
 * - Action (category chips — existing)
 * - Actor (substring on user email OR token name — new)
 * - Target kind (exact match — new)
 * - Since / Until (ISO dates — new)
 *
 * Filters are submitted via a plain HTML `<form method="GET">` so the
 * page stays a Server Component — no `useState`, no `useRouter`, just
 * the browser's native "submit → new URL → server rerender" loop. The
 * only client bit is the payload expander row (tiny; lives in
 * `audit-payload-cell.tsx`).
 *
 * Pagination preserves every active filter — we push them into the
 * `Older →` link so cursor-walking doesn't silently widen the view.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import {
  Badge,
  Card,
  CardHeader,
  LiveBanner,
} from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  listAuditLog,
} from "@/lib/api/client";
import type { ApiAuditEntry, ApiAuditPage, ApiWorkspace } from "@/lib/api/types";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  pickWorkspace,
  withWorkspaceQuery,
} from "@/lib/workspace-scope";

import { AuditPayloadCell } from "./audit-payload-cell";

export const dynamic = "force-dynamic";

/**
 * Action filter chips. `id` is what the backend expects; `""` is the
 * "no filter" sentinel (we strip it from the URL on submit).
 *
 * Keep this in lock-step with `_ALLOWED_ACTION_PREFIXES` in
 * `backend/app/api/v1/routes/audit.py` — the backend 422s on anything
 * that isn't a known prefix or fully-qualified value, so adding a chip
 * here without extending the backend list would give us an empty page
 * with a silent 422.
 */
const ACTION_FILTERS = [
  { id: "", label: "All" },
  { id: "workspace", label: "Workspace" },
  { id: "member", label: "Members" },
  { id: "auth", label: "Auth & tokens" },
  { id: "integration", label: "Integrations" },
  { id: "artifact_repo", label: "Artifact repos" },
  { id: "pipeline", label: "Pipelines" },
  { id: "repo", label: "Repos" },
  { id: "improvement", label: "Improvements" },
  { id: "clarification", label: "Clarifications" },
  { id: "invite", label: "Invites" },
  { id: "agent", label: "Agent" },
] as const;

/**
 * Target-kind options for the `<select>` filter. Mirrors the
 * `_ALLOWED_TARGET_KINDS` frozenset on the backend — again, anything
 * not on this list 422s server-side, so keep them in sync.
 */
const TARGET_KINDS = [
  { id: "", label: "Any" },
  { id: "workspace", label: "Workspace" },
  { id: "user", label: "User" },
  { id: "api_token", label: "API token" },
  { id: "integration", label: "Integration" },
  { id: "artifact_repo", label: "Artifact repo" },
  { id: "pipeline", label: "Pipeline" },
  { id: "pipeline_run", label: "Pipeline run" },
  { id: "workspace_repo", label: "Workspace repo" },
  { id: "workspace_repos", label: "Workspace repos (bulk)" },
  { id: "workspace_invite", label: "Invite" },
  { id: "github_installation", label: "GitHub install" },
  { id: "improvement", label: "Improvement" },
  { id: "clarification", label: "Clarification" },
] as const;

const PAGE_SIZE = 50;

interface AuditFilters {
  action: string;
  actor: string;
  targetKind: string;
  since: string;
  until: string;
  before: number | null;
}

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      allWorkspaces: ApiWorkspace[];
      page: ApiAuditPage;
      filters: AuditFilters;
    }
  | { source: "error"; reason: string; redirectReason?: string };

function readParam(
  params: Record<string, string | string[] | undefined>,
  name: string,
): string {
  const v = params[name];
  return typeof v === "string" ? v : "";
}

function parseFilters(
  params: Record<string, string | string[] | undefined>,
): AuditFilters {
  const beforeRaw = readParam(params, "before");
  const before = beforeRaw && /^\d+$/.test(beforeRaw) ? Number(beforeRaw) : null;
  return {
    action: readParam(params, "action").trim(),
    actor: readParam(params, "actor").trim(),
    targetKind: readParam(params, "target_kind").trim(),
    since: readParam(params, "since").trim(),
    until: readParam(params, "until").trim(),
    before,
  };
}

async function load(
  filters: AuditFilters,
  params: Record<string, string | string[] | undefined>,
): Promise<Mode> {
  const token = await getCachedSessionToken();
  if (!token)
    return { source: "error", reason: "Sign in to view audit history.", redirectReason: "session_expired" };
  try {
    const ws = await getCachedWorkspaces();
    const resolved = await getResolvedWorkspaceId(params, ws);
    const target = pickWorkspace(ws, resolved);
    const page = await listAuditLog(
      target.id,
      {
        limit: PAGE_SIZE,
        before: filters.before,
        action: filters.action || null,
        actor: filters.actor || null,
        target_kind: filters.targetKind || null,
        since: filters.since || null,
        until: filters.until || null,
      },
      token,
    );
    return { source: "live", workspace: target, allWorkspaces: ws, page, filters };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      return { source: "error", reason: "Session expired — sign in again.", redirectReason: "session_expired" };
    if (err instanceof ApiHttpError && err.status === 403)
      return {
        source: "error",
        reason: "Audit log is owner / admin only — ask your workspace admin.",
      };
    if (err instanceof ApiHttpError && err.status === 422)
      return {
        source: "error",
        reason: `Backend rejected a filter value (${err.message}). Check date shape + categories.`,
      };
    if (err instanceof ApiUnavailableError)
      return { source: "error", reason: "Backend unreachable." };
    return { source: "error", reason: err instanceof Error ? err.message : "Backend returned an error." };
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

function buildPagerUrl(filters: AuditFilters, cursor: number | null): string {
  // Always preserve the current filter set when paging; `before` is the
  // only param that flips (cursor walk).
  const params = new URLSearchParams();
  if (filters.action) params.set("action", filters.action);
  if (filters.actor) params.set("actor", filters.actor);
  if (filters.targetKind) params.set("target_kind", filters.targetKind);
  if (filters.since) params.set("since", filters.since);
  if (filters.until) params.set("until", filters.until);
  if (cursor !== null) params.set("before", String(cursor));
  const q = params.toString();
  return q ? `/audit?${q}` : "/audit";
}

function hasFilters(f: AuditFilters): boolean {
  return !!(f.action || f.actor || f.targetKind || f.since || f.until);
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
  const filters = parseFilters(params);
  const data = await load(filters, params);

  if (data.source === "error") {
    if (data.redirectReason === "session_expired") {
      redirect("/login?next=%2Faudit&reason=session_expired");
    }
    return (
      <>
        <PageHeader title="Audit" />
        <PageBody>
          <ApiUnavailable scope="audit" details={data.reason} />
        </PageBody>
      </>
    );
  }

  const { workspace, allWorkspaces, page } = data;
  const multiWorkspace = allWorkspaces.length > 1;
  const items = page.items;
  const nextHref =
    page.next_cursor !== null ? buildPagerUrl(filters, page.next_cursor) : null;

  return (
    <>
      <PageHeader
        title="Audit"
        actions={
          <Link
            href={withWorkspaceQuery("/audit", workspace.id, multiWorkspace)}
            className="rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-bold text-white/85 transition hover:bg-white/[0.08]"
          >
            Reset
          </Link>
        }
      />
      <PageBody>
        <LiveBanner workspace={workspace.slug} />

      <FilterCard filters={filters} />

      <Card padded={false} className="overflow-hidden">
        <CardHeader
          className="px-5 pt-5"
          title={`History · ${items.length} row${items.length === 1 ? "" : "s"}`}
          subtitle={
            hasFilters(filters)
              ? "Filtered view — reset to see the full workspace history."
              : "Newest first. Each row is one privileged mutation; the JSON payload is recorded verbatim."
          }
        />
        {items.length === 0 ? (
          <Card className="mx-5 my-5 border border-white/10 bg-white/[0.02]">
            <div className="px-5 py-8 text-center">
              <h4 className="font-display text-base font-bold text-white">
                No audit events yet
              </h4>
              <p className="mt-2 text-sm text-white/65">
                Audit lights up after the first action in this workspace.
              </p>
            </div>
          </Card>
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
                  <td className="px-4 py-3 text-xs text-white/65 whitespace-nowrap">
                    {new Date(entry.created_at).toUTCString()}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <Badge tone={actionTone(entry.action)} dot>
                      {entry.action}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-xs text-white/85">
                    {actorLabel(entry)}
                  </td>
                  <td className="px-4 py-3 text-xs text-white/65 whitespace-nowrap">
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
                    <AuditPayloadCell payload={entry.payload} />
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
      </PageBody>
    </>
  );
}

function FilterCard({ filters }: { filters: AuditFilters }) {
  return (
    <Card className="mb-5">
      <CardHeader
        title="Filter"
        subtitle="Combine any filters below. Submit to apply — empty fields are ignored. Pagination keeps the filter set."
      />
      <form
        method="GET"
        action="/audit"
        className="flex flex-col gap-4"
      >
        <div className="flex flex-wrap gap-2">
          {ACTION_FILTERS.map((opt) => {
            const active = (opt.id || "") === filters.action;
            return (
              <label
                key={opt.id || "all"}
                className={
                  "cursor-pointer rounded-full border px-3 py-1 text-xs font-semibold transition " +
                  (active
                    ? "border-aqua/50 bg-aqua/15 text-aqua"
                    : "border-white/15 bg-white/[0.04] text-white/75 hover:bg-white/[0.08]")
                }
              >
                <input
                  type="radio"
                  name="action"
                  value={opt.id}
                  defaultChecked={active}
                  className="sr-only"
                />
                {opt.label}
              </label>
            );
          })}
        </div>
        <div className="grid gap-3 md:grid-cols-4">
          <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-widest text-white/55">
            Actor
            <input
              type="text"
              name="actor"
              defaultValue={filters.actor}
              placeholder="email or token name"
              className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-sm font-normal normal-case tracking-normal text-white/90 focus:border-aqua/50 focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-widest text-white/55">
            Target kind
            <select
              name="target_kind"
              defaultValue={filters.targetKind}
              className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-sm font-normal normal-case tracking-normal text-white/90 focus:border-aqua/50 focus:outline-none"
            >
              {TARGET_KINDS.map((t) => (
                <option key={t.id || "any"} value={t.id} className="bg-ink">
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-widest text-white/55">
            Since (UTC)
            <input
              type="date"
              name="since"
              defaultValue={filters.since}
              className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-sm font-normal normal-case tracking-normal text-white/90 focus:border-aqua/50 focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-widest text-white/55">
            Until (UTC)
            <input
              type="date"
              name="until"
              defaultValue={filters.until}
              className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-sm font-normal normal-case tracking-normal text-white/90 focus:border-aqua/50 focus:outline-none"
            />
          </label>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="submit"
            className="rounded-full bg-aqua/80 px-4 py-1.5 text-xs font-bold text-ink transition hover:bg-aqua"
          >
            Apply filters
          </button>
          <Link
            href="/audit"
            className="rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-bold text-white/85 transition hover:bg-white/[0.08]"
          >
            Clear
          </Link>
          {hasFilters(filters) ? (
            <span className="text-[11px] text-white/45">
              Active: {[
                filters.action && `action=${filters.action}`,
                filters.actor && `actor=${filters.actor}`,
                filters.targetKind && `target=${filters.targetKind}`,
                filters.since && `since=${filters.since}`,
                filters.until && `until=${filters.until}`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          ) : null}
        </div>
      </form>
    </Card>
  );
}

