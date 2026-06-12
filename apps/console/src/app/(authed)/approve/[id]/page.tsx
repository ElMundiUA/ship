/**
 * `/approve/{id}` — the console's only remaining inbox surface
 * (ELS-294, MCP-first rework).
 *
 * One inbox item, reached only by deep-link: Telegram approval buttons
 * and MCP `web_url` refusals land here, and the operator hub's
 * "Waiting on you" strip links here. No list view, no filters, no
 * mailbox chrome — day-to-day inbox triage happens in the operator's
 * agent over MCP (`inbox_list` / `inbox_update`) and in Telegram.
 *
 * Renders title, summary/body, actor attribution, and the disposition
 * footer. Items whose payload carries `stakes: destructive` (or
 * `web_only`) get the typed-slug confirm — by stakes policy this page
 * is the ONLY place they can be approved; MCP and Telegram refuse
 * them at the gate. Already-disposed items render read-only.
 *
 * Reachable in EVERY `console.surface` mode (see console-mode.ts) so
 * pending approvals are never orphaned (thesis 4).
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { PageBody, PageHeader } from "@/components/app-shell";
import { InboxActionPanel } from "@/components/inbox/inbox-action-panel";
import { InboxDispositionPanel } from "@/components/inbox/inbox-disposition-panel";
import { MailboxFooter } from "@/components/inbox/mailbox-footer";
import { inboxItemUrl } from "@/components/inbox/inbox-url";
import { MarkdownBlock } from "@/components/markdown-block";
import { formatInboxHeadline } from "@/lib/inbox-copy";
import { relativeTime } from "@/lib/format";
import { ApiHttpError, getInboxItem } from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  INBOX_TYPE_META,
  inboxRowKicker,
  isInboxType,
  parseChecklistActionItems,
  type InboxItemDetail,
} from "@/lib/inbox-types";
import { pickWorkspace } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

/** Stakes values the MCP edge refuses and routes here (mirror of the
 * backend's WEB_ONLY_STAKES — keep in sync with routes/mcp.py). */
const WEB_ONLY_STAKES = new Set(["destructive", "web_only"]);

function errorMessage(code: string): string {
  switch (code) {
    case "forbidden":
      return "You don't have permission to act on this item.";
    case "not_found":
      return "This item is gone — it may have been resolved elsewhere.";
    case "validation_failed":
      return "Reply was empty — write something before sending.";
    case "state_invalid":
      return "Item state changed since you opened it. Refresh and try again.";
    case "api_unavailable":
      return "Backend is unreachable. Try again in a moment.";
    default:
      return `Couldn't apply the change (${code}).`;
  }
}

export default async function ApprovePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const query = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const errorCode = typeof query.error === "string" ? query.error : null;

  const token = await getCachedSessionToken();
  if (!token) {
    redirect(
      `/login?next=${encodeURIComponent(`/approve/${id}`)}&reason=session_expired`,
    );
  }
  const ws = await getCachedWorkspaces();
  if (ws.length === 0) redirect("/onboarding?step=github");
  const resolved = await getResolvedWorkspaceId(query, ws);
  const workspace = pickWorkspace(ws, resolved);
  const wsScope = ws.length > 1 ? workspace.id : undefined;

  let detail: InboxItemDetail | null = null;
  let loadError: "not_found" | "load_failed" | null = null;
  try {
    detail = await getInboxItem(workspace.id, id, token);
  } catch (err) {
    loadError =
      err instanceof ApiHttpError && err.status === 404
        ? "not_found"
        : "load_failed";
  }

  return (
    <>
      <PageHeader kicker="approval" title="Waiting on you" />
      <PageBody>
        <div className="mx-auto w-full max-w-2xl">
          {errorCode && (
            <p className="mb-4 text-xs text-coral/95">{errorMessage(errorCode)}</p>
          )}
          {detail ? (
            <ApproveCard
              detail={detail}
              workspaceId={workspace.id}
              wsScope={wsScope}
            />
          ) : (
            <div
              className="rounded-xl border border-white/[0.08] bg-white/[0.015] px-6 py-10 text-center text-sm text-white/55"
              data-testid="approve-missing"
            >
              {loadError === "not_found"
                ? "This item is gone — it was already resolved or never existed."
                : "Couldn't load this item right now. Refresh in a moment."}
            </div>
          )}
          <p className="mt-4 text-center text-[11px] text-white/40">
            <Link href={wsScope ? `/?ws=${wsScope}` : "/"} className="hover:text-white/70">
              ← Back to the hub
            </Link>
          </p>
        </div>
      </PageBody>
    </>
  );
}

function ApproveCard({
  detail,
  workspaceId,
  wsScope,
}: {
  detail: InboxItemDetail;
  workspaceId: string;
  wsScope?: string;
}) {
  const meta = isInboxType(detail.type) ? INBOX_TYPE_META[detail.type] : null;
  const kicker = inboxRowKicker(detail.type);
  const body = readBody(detail);
  const isClosed =
    detail.status === "resolved" || detail.status === "dismissed";
  const headline = formatInboxHeadline(detail);
  const checklistItems = parseChecklistActionItems(detail.payload);
  const rawActionItems = (detail.payload as Record<string, unknown> | undefined)?.[
    "action_items"
  ];
  const hasActionItems =
    Array.isArray(rawActionItems) && rawActionItems.length > 0;
  const stakes = String(
    (detail.payload as Record<string, unknown> | undefined)?.["stakes"] ?? "",
  ).toLowerCase();
  const destructive = WEB_ONLY_STAKES.has(stakes);
  const returnTo = inboxItemUrl(detail.id, wsScope);

  return (
    <div
      className="flex flex-col rounded-xl border border-white/[0.08] bg-white/[0.015]"
      data-testid="approve-card"
    >
      <header className="border-b border-white/[0.06] px-6 py-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] uppercase tracking-[0.18em] text-white/45">
          <span>{meta?.label ?? kicker.label}</span>
          {destructive && (
            <>
              <span className="text-white/15">·</span>
              <span className="text-coral/90">destructive</span>
            </>
          )}
          <span className="text-white/15">·</span>
          <span
            className="text-white/55 normal-case tracking-normal"
            title={new Date(detail.created_at).toLocaleString()}
          >
            received {relativeTime(detail.created_at)}
          </span>
          {isClosed && (
            <>
              <span className="text-white/15">·</span>
              <span className="text-white/55">{detail.status}</span>
              {detail.resolution && (
                <>
                  <span className="text-white/15">·</span>
                  <span className="text-white/55">{detail.resolution}</span>
                </>
              )}
            </>
          )}
        </div>
        <h1 className="mt-1 text-lg font-semibold text-white">{headline}</h1>
        {detail.owner && (
          <p className="mt-1 text-xs text-white/55">
            Owner:{" "}
            <span className="text-white/75">
              {detail.owner.display_name?.trim() || detail.owner.email}
            </span>
          </p>
        )}
      </header>

      <div className="max-h-[70vh] overflow-y-auto px-6 py-5">
        {body ? (
          <MarkdownBlock collapseFromHeadings={["Technical details", "Details"]}>
            {body}
          </MarkdownBlock>
        ) : (
          <p className="text-sm italic text-white/40">No body.</p>
        )}
      </div>

      <footer className="space-y-3 border-t border-white/[0.06] px-6 py-4">
        {isClosed ? (
          <p className="text-xs italic text-white/40" data-testid="approve-resolved">
            Already {detail.status} — nothing left to do here.
          </p>
        ) : checklistItems.length > 0 ? (
          <MailboxFooter
            detail={detail}
            workspaceId={workspaceId}
            returnTo={returnTo}
          />
        ) : hasActionItems ? (
          <InboxActionPanel workspaceId={workspaceId} item={detail} />
        ) : (
          <InboxDispositionPanel
            workspaceId={workspaceId}
            itemId={detail.id}
            itemType={isInboxType(detail.type) ? detail.type : "clarification"}
            returnTo={returnTo}
            destructive={destructive}
          />
        )}
      </footer>
    </div>
  );
}

function readBody(detail: InboxItemDetail): string {
  // Agent-filed reports stash the markdown body under
  // ``payload.body``; legacy items keep the human-readable text in
  // ``summary``. Fall through gracefully.
  const payloadBody = detail.payload?.["body"];
  if (typeof payloadBody === "string" && payloadBody.trim().length > 0) {
    return payloadBody;
  }
  if (detail.summary && detail.summary.trim().length > 0) return detail.summary;
  return "";
}
