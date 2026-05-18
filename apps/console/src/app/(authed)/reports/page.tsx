/**
 * Reports — read-only inbox digests (daily / retro / process review).
 *
 * Same mailbox disposition surface as `/inbox`, but lists only
 * ``category=attention`` + ``type=report`` rows grouped by day and
 * routine (``play_key``).
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import { MailboxFooter } from "@/components/inbox/mailbox-footer";
import { MarkdownBlock } from "@/components/markdown-block";
import { cn } from "@/lib/cn";
import {
  ApiHttpError,
  getInboxItem,
  listInboxItems,
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  INBOX_LIST_DEFAULT_STATUSES,
  INBOX_TYPE_META,
  type InboxItem,
  type InboxItemDetail,
  type InboxListResponse,
  type InboxType,
} from "@/lib/inbox-types";
import { pickWorkspace } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

const PAGE_LIMIT = 100;

type ParsedParams = {
  selectedId: string | null;
  errorCode: string | null;
};

function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

function groupReports(items: InboxItem[]): { day: string; groups: { routine: string; items: InboxItem[] }[] }[] {
  const byDay = new Map<string, Map<string, InboxItem[]>>();
  for (const item of items) {
    const day = dayKey(item.created_at);
    const routine = item.play_key?.trim() || "General";
    if (!byDay.has(day)) byDay.set(day, new Map());
    const routines = byDay.get(day)!;
    if (!routines.has(routine)) routines.set(routine, []);
    routines.get(routine)!.push(item);
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => (a < b ? 1 : -1))
    .map(([day, routines]) => ({
      day,
      groups: [...routines.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([routine, groupItems]) => ({ routine, items: groupItems })),
    }));
}

export default async function ReportsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const selectedRaw = typeof params.selected === "string" ? params.selected : null;
  const parsed: ParsedParams = {
    selectedId: selectedRaw && selectedRaw.length > 0 ? selectedRaw : null,
    errorCode: typeof params.error === "string" ? params.error : null,
  };

  const token = await getCachedSessionToken();
  if (!token) redirect("/login?next=%2Freports&reason=session_expired");

  const ws = await getCachedWorkspaces();
  const resolved = await getResolvedWorkspaceId(params, ws);
  const workspace = pickWorkspace(ws, resolved);
  const multiWs = ws.length > 1;
  const wsScope = multiWs ? workspace.id : undefined;

  let list: InboxListResponse;
  try {
    list = await listInboxItems(
      workspace.id,
      {
        ownership: "all",
        categories: ["attention"],
        types: ["report"],
        statuses: INBOX_LIST_DEFAULT_STATUSES,
        sort: "created_desc",
        limit: PAGE_LIMIT,
      },
      token,
    );
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Freports&reason=session_expired");
    }
    return (
      <>
        <PageHeader kicker="attention" title="Reports" />
        <PageBody>
          <ApiUnavailable
            scope="reports"
            details={err instanceof Error ? err.message : "Could not load reports."}
          />
        </PageBody>
      </>
    );
  }

  const effectiveSelected = parsed.selectedId ?? list.items[0]?.id ?? null;
  let detail: InboxItemDetail | null = null;
  let detailError: string | null = null;
  if (effectiveSelected) {
    try {
      detail = await getInboxItem(workspace.id, effectiveSelected, token);
    } catch (err) {
      detailError =
        err instanceof ApiHttpError && err.status === 404 ? "not_found" : "load_failed";
    }
  }

  const grouped = groupReports(list.items);

  return (
    <>
      <PageHeader kicker="attention" title="Reports" />
      <PageBody>
        <div className="grid gap-4 lg:grid-cols-[26rem_minmax(0,1fr)]">
          <ReportsList
            grouped={grouped}
            selectedId={effectiveSelected}
            workspaceScope={wsScope}
          />
          <ReportsPreview
            detail={detail}
            detailError={detailError}
            workspaceId={workspace.id}
            empty={list.items.length === 0}
          />
        </div>
      </PageBody>
    </>
  );
}

function buildSelectHref(id: string, workspaceScope?: string): string {
  const params = new URLSearchParams();
  params.set("selected", id);
  if (workspaceScope) params.set("ws", workspaceScope);
  const qs = params.toString();
  return qs ? `/reports?${qs}` : "/reports";
}

function ReportsList({
  grouped,
  selectedId,
  workspaceScope,
}: {
  grouped: ReturnType<typeof groupReports>;
  selectedId: string | null;
  workspaceScope?: string;
}) {
  if (grouped.length === 0) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center rounded-xl border border-white/[0.08] bg-white/[0.015] px-4 text-sm text-white/55">
        No reports yet.
      </div>
    );
  }
  return (
    <div
      className="flex min-h-[60vh] flex-col overflow-y-auto rounded-xl border border-white/[0.08] bg-white/[0.015]"
      data-testid="reports-list"
    >
      {grouped.map(({ day, groups }) => (
        <section key={day} className="border-b border-white/[0.06] last:border-0">
          <h2 className="sticky top-0 z-10 bg-black/40 px-4 py-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-white/45 backdrop-blur">
            {day}
          </h2>
          {groups.map(({ routine, items }) => (
            <RoutineGroup
              key={`${day}-${routine}`}
              routine={routine}
              items={items}
              selectedId={selectedId}
              workspaceScope={workspaceScope}
            />
          ))}
        </section>
      ))}
    </div>
  );
}

function RoutineGroup({
  routine,
  items,
  selectedId,
  workspaceScope,
}: {
  routine: string;
  items: InboxItem[];
  selectedId: string | null;
  workspaceScope?: string;
}) {
  return (
    <div className="px-2 pb-3">
      <p className="px-2 py-1 text-[10px] uppercase tracking-wider text-white/35">
        {routine}
      </p>
      <ul className="divide-y divide-white/[0.05]">
        {items.map((item) => (
          <li key={item.id}>
            <Link
              href={buildSelectHref(item.id, workspaceScope)}
              className={cn(
                "block rounded-lg px-3 py-2 text-sm transition",
                item.id === selectedId
                  ? "bg-aqua/[0.08] font-semibold text-white"
                  : "text-white/75 hover:bg-white/[0.03]",
              )}
            >
              {item.title}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReportsPreview({
  detail,
  detailError,
  workspaceId,
  empty,
}: {
  detail: InboxItemDetail | null;
  detailError: string | null;
  workspaceId: string;
  empty: boolean;
}) {
  if (empty) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center rounded-xl border border-white/[0.08] bg-white/[0.015] px-6 text-sm text-white/55">
        Daily and retro digests land here when routines emit them.
      </div>
    );
  }
  if (detailError === "not_found" || !detail) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center rounded-xl border border-white/[0.08] bg-white/[0.015] px-6 text-sm text-white/55">
        Pick a report on the left to read it.
      </div>
    );
  }
  const meta = INBOX_TYPE_META[detail.type as InboxType];
  const body = readBody(detail);
  const isClosed = detail.status === "resolved" || detail.status === "dismissed";

  return (
    <div className="flex min-h-[60vh] flex-col rounded-xl border border-white/[0.08] bg-white/[0.015]">
      <header className="border-b border-white/[0.06] px-6 py-4">
        <p className="text-[10px] uppercase tracking-[0.18em] text-white/45">
          {meta?.label ?? detail.type}
        </p>
        <h2 className="mt-1 font-display text-lg font-semibold text-white">
          {detail.title}
        </h2>
      </header>
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {body ? (
          <MarkdownBlock>{body}</MarkdownBlock>
        ) : (
          <p className="text-sm text-white/55">No body on this report.</p>
        )}
      </div>
      {!isClosed && (
        <footer className="border-t border-white/[0.06] px-6 py-4">
          <MailboxFooter detail={detail} workspaceId={workspaceId} />
        </footer>
      )}
    </div>
  );
}

function readBody(detail: InboxItemDetail): string {
  const payload = detail.payload ?? {};
  const payloadBody = payload.body ?? payload.markdown ?? payload.content;
  if (typeof payloadBody === "string" && payloadBody.trim().length > 0) {
    return payloadBody;
  }
  if (detail.summary && detail.summary.trim().length > 0) return detail.summary;
  return "";
}
