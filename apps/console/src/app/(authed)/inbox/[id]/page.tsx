/**
 * Inbox item detail page (RFC-0010 P2-13).
 *
 * Single-item disposition surface that drives an inbox item through
 * its lifecycle (resolve / dismiss / approve / reject / answer /
 * accept / retry / acknowledge), plus snooze, reassign, and free-text
 * comments. Server component, every mutation goes through a tiny
 * ``<form action="/api/inbox/[id]/...">`` POST so the page stays
 * useful with JS off and degrades gracefully when the backend isn't
 * configured (MockView fallback).
 *
 * Layout: header card + disposition card + payload card + events
 * timeline + comment form in the main column; owner card + source
 * card in the sidebar. The disposition card swaps for a "Resolved"
 * confirmation (or a "Wake up now" button when snoozed) so the same
 * surface keeps reading correctly across every state.
 */

import Link from "next/link";
import { notFound } from "next/navigation";

import { PageBody, PageHeader } from "@/components/app-shell";
import { ApiUnavailable } from "@/components/api-unavailable";
import { StaleBadge } from "@/components/inbox/stale-badge";
import { MarkdownBlock } from "@/components/markdown-block";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  getInboxItem,
  isApiConfigured,
  listMembers,
} from "@/lib/api/client";
import type { ApiMember, ApiUser, ApiWorkspace } from "@/lib/api/types";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";
import {
  INBOX_TYPE_META,
  reportActionItemDecisions,
  reportActionItems,
  type InboxItemDetail,
  type InboxItemEvent,
  type InboxType,
} from "@/lib/inbox-types";

export const dynamic = "force-dynamic";

// ---------------------------------------------------------------------------
// Types + small per-type metadata
// ---------------------------------------------------------------------------

type DispositionAction =
  | "resolve"
  | "dismiss"
  | "approve"
  | "reject"
  | "answer"
  | "accept"
  | "retry"
  | "acknowledge";

type ButtonStyle = "primary" | "secondary" | "danger";

type ButtonSpec = {
  action: DispositionAction;
  label: string;
  style: ButtonStyle;
  /** Confirm prompt for destructive actions (Dismiss / Reject). */
  confirm?: string;
};

/**
 * Per-type disposition vocabulary.
 *
 * Mirrors the planning doc table — the primary action expresses the
 * "what should happen by default" verb for that type; secondaries
 * cover the alternate paths. ``failure`` lists "Acknowledge"
 * conceptually but submits the open-ended ``resolve`` action since
 * the backend type-gates ``acknowledge`` to exceptions only (see
 * ``_TYPE_GATED_ACTIONS`` in backend/app/api/v1/routes/inbox.py).
 */
const DISPOSITION_BY_TYPE: Record<
  InboxType,
  { primary: ButtonSpec; secondary: ButtonSpec[] }
> = {
  clarification: {
    primary: { action: "answer", label: "Answer", style: "primary" },
    secondary: [
      {
        action: "dismiss",
        label: "Dismiss",
        style: "danger",
        confirm: "Dismiss this clarification without answering?",
      },
    ],
  },
  approval: {
    primary: { action: "approve", label: "Approve", style: "primary" },
    secondary: [
      { action: "reject", label: "Reject", style: "danger" },
      {
        action: "dismiss",
        label: "Dismiss",
        style: "secondary",
        confirm: "Dismiss this approval request?",
      },
    ],
  },
  improvement: {
    primary: { action: "accept", label: "Accept", style: "primary" },
    secondary: [
      {
        action: "dismiss",
        label: "Dismiss",
        style: "danger",
        confirm: "Dismiss this improvement?",
      },
    ],
  },
  failure: {
    primary: { action: "retry", label: "Retry", style: "primary" },
    secondary: [
      // ``resolve`` rather than ``acknowledge`` — see file-level note
      // and the action_resolution table in the backend route.
      { action: "resolve", label: "Acknowledge", style: "secondary" },
      {
        action: "dismiss",
        label: "Dismiss",
        style: "danger",
        confirm: "Dismiss this failure?",
      },
    ],
  },
  exception: {
    primary: { action: "acknowledge", label: "Acknowledge", style: "primary" },
    secondary: [
      {
        action: "dismiss",
        label: "Dismiss",
        style: "danger",
        confirm: "Dismiss this exception?",
      },
    ],
  },
  stuck: {
    primary: { action: "resolve", label: "Mark addressed", style: "primary" },
    secondary: [
      {
        action: "dismiss",
        label: "Dismiss",
        style: "danger",
        confirm: "Dismiss this stuck-work reminder?",
      },
    ],
  },
  blocker: {
    primary: { action: "resolve", label: "Mark handled", style: "primary" },
    secondary: [
      {
        action: "dismiss",
        label: "Dismiss",
        style: "danger",
        confirm: "Dismiss this blocker record?",
      },
    ],
  },
  report: {
    // ``report`` is the read-only digest type (daily / retro / process
    // review). The only operator action is mark-read; ``resolve``
    // writes resolution=acknowledged via the open-ended path. No
    // dismiss — there's nothing to refuse, just letters to read.
    primary: { action: "resolve", label: "Acknowledge", style: "primary" },
    secondary: [],
  },
};

// ---------------------------------------------------------------------------
// Loader
// ---------------------------------------------------------------------------

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      detail: InboxItemDetail;
      members: ApiMember[];
    }
  | { source: "mock"; reason: string };

async function load(
  itemId: string,
  searchParams: Record<string, string | string[] | undefined>,
): Promise<Mode> {
  if (!isApiConfigured())
    return { source: "mock", reason: "SHIP_API_URL not set" };
  const token = await getCachedSessionToken();
  if (!token)
    return { source: "mock", reason: "Sign in to view real inbox items" };

  const workspaces = await getCachedWorkspaces();
  const resolved = await getResolvedWorkspaceId(searchParams, workspaces);
  const workspace = pickWorkspace(workspaces, resolved);

  let detail: InboxItemDetail;
  try {
    detail = await getInboxItem(workspace.id, itemId, token);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return { source: "mock", reason: "Session expired — sign in again" };
      }
      if (err.status === 404) {
        // The item belongs to a different workspace OR no longer
        // exists — surface Next.js's 404 surface so navigation
        // history reads the URL as gone rather than as broken.
        notFound();
      }
    }
    if (err instanceof ApiUnavailableError) {
      return { source: "mock", reason: "Backend unreachable" };
    }
    return { source: "mock", reason: "Couldn't load item" };
  }

  let members: ApiMember[];
  try {
    members = await listMembers(workspace.id, token);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return { source: "mock", reason: "Backend unreachable" };
    }
    members = [];
  }

  return { source: "live", workspace, detail, members };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default async function InboxItemPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const errorCode = typeof sp.error === "string" ? sp.error : null;

  const data = await load(id, sp);
  if (data.source === "mock") {
    return <MockView reason={data.reason} errorCode={errorCode} id={id} />;
  }

  const { workspace, detail, members } = data;
  const meta = INBOX_TYPE_META[detail.type as InboxType];

  return (
    <>
      <PageHeader
        kicker={meta?.label ?? detail.type}
        title={detail.title}
        actions={
          <Link
            href="/inbox"
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            ← Inbox
          </Link>
        }
      />
      <PageBody>
        {errorCode && (
          <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
            {errorMessage(errorCode)}
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="space-y-4">
            <HeaderCard detail={detail} />
            <SourceTicketCard detail={detail} />
            <DispositionCard
              detail={detail}
              workspaceId={workspace.id}
            />
            <EventsCard
              events={detail.events}
              members={members}
            />
            <CommentCard workspaceId={workspace.id} itemId={detail.id} />
          </div>

          <aside className="space-y-4">
            <OwnerCard
              detail={detail}
              workspaceId={workspace.id}
              members={members}
            />
            <SourceCard detail={detail} />
          </aside>
        </div>
      </PageBody>
    </>
  );
}

// ---------------------------------------------------------------------------
// Header card
// ---------------------------------------------------------------------------

function HeaderCard({ detail }: { detail: InboxItemDetail }) {
  return (
    <Card>
      <div className="flex flex-wrap items-start gap-2">
        <StatusBadge status={detail.status} />
        <StaleBadge
          createdAt={detail.created_at}
          status={detail.status as "new" | "snoozed" | "resolved" | "dismissed"}
          snoozedUntil={detail.snoozed_until}
        />
      </div>
      {detail.summary ? (
        <div className="mt-3">
          <MarkdownBlock>{detail.summary}</MarkdownBlock>
        </div>
      ) : null}
      {(detail.due_at || detail.snoozed_until || detail.resolved_at) && (
        <dl className="mt-4 grid grid-cols-1 gap-2 text-xs text-white/65 sm:grid-cols-2">
          {detail.due_at && <Row label="Due">{absolute(detail.due_at)}</Row>}
          {detail.snoozed_until && (
            <Row label="Snoozed until">{absolute(detail.snoozed_until)}</Row>
          )}
          {detail.resolved_at && (
            <Row label="Resolved at">{absolute(detail.resolved_at)}</Row>
          )}
        </dl>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Disposition card — primary + secondary buttons OR resolved confirmation
// ---------------------------------------------------------------------------

function DispositionCard({
  detail,
  workspaceId,
}: {
  detail: InboxItemDetail;
  workspaceId: string;
}) {
  if (detail.status === "resolved" || detail.status === "dismissed") {
    return <ResolvedCard detail={detail} />;
  }

  const reportItems = reportActionItems(detail.payload);
  const reportDecided = reportActionItemDecisions(detail.payload);
  const pendingReportItems = reportItems.filter((item) => !reportDecided[item.id]);
  const vocabulary =
    detail.type === "report" && pendingReportItems.length > 0
      ? null
      : DISPOSITION_BY_TYPE[detail.type as InboxType];

  return (
    <Card>
      <CardHeader
        title={detail.status === "snoozed" ? "Pick up where you left off" : "Disposition"}
        subtitle={
          detail.status === "snoozed"
            ? "Item is parked. Wake it up to act, or close it out from here."
            : "Drive this item to one of the lifecycle terminals below."
        }
      />

      {detail.status === "snoozed" && (
        <form
          action={`/api/inbox/${encodeURIComponent(detail.id)}/unsnooze`}
          method="POST"
          className="mb-4"
        >
          <input type="hidden" name="ws" value={workspaceId} />
          <button
            type="submit"
            className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/15 px-3.5 py-1.5 text-xs font-bold text-aqua transition hover:bg-aqua/25"
          >
            ↻ Wake up now
          </button>
        </form>
      )}

      {pendingReportItems.length > 0 ? (
        <div className="space-y-3">
          {pendingReportItems.map((item) => (
            <ReportActionItemBlock
              key={item.id}
              workspaceId={workspaceId}
              itemId={detail.id}
              item={item}
            />
          ))}
        </div>
      ) : vocabulary ? (
        <div className="space-y-4">
          {vocabulary.primary.action === "answer" ? (
            <AnswerForm workspaceId={workspaceId} itemId={detail.id} />
          ) : (
            <PrimaryActionForm
              workspaceId={workspaceId}
              itemId={detail.id}
              spec={vocabulary.primary}
            />
          )}

          {vocabulary.secondary.length > 0 && (
            <div className="flex flex-wrap gap-2 border-t border-white/10 pt-4">
              {vocabulary.secondary.map((spec) => (
                <SecondaryActionForm
                  key={spec.action + spec.label}
                  workspaceId={workspaceId}
                  itemId={detail.id}
                  spec={spec}
                />
              ))}
            </div>
          )}
        </div>
      ) : (
        <p className="text-xs italic text-white/55">
          Unknown item type — only generic resolve / dismiss are
          available below.
        </p>
      )}

      {detail.status === "new" && (
        <SnoozeForm workspaceId={workspaceId} itemId={detail.id} />
      )}
    </Card>
  );
}

function ReportActionItemBlock({
  workspaceId,
  itemId,
  item,
}: {
  workspaceId: string;
  itemId: string;
  item: { id: string; prompt: string; primary: string; secondary: string };
}) {
  return (
    <div className="rounded border border-white/10 bg-white/[0.04] p-3">
      <p className="text-sm text-white/90">{item.prompt}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <ReportActionItemButton
          workspaceId={workspaceId}
          itemId={itemId}
          actionItemId={item.id}
          choice="primary"
          label={item.primary}
          style="primary"
        />
        <ReportActionItemButton
          workspaceId={workspaceId}
          itemId={itemId}
          actionItemId={item.id}
          choice="secondary"
          label={item.secondary}
          style="danger"
        />
      </div>
    </div>
  );
}

function ReportActionItemButton({
  workspaceId,
  itemId,
  actionItemId,
  choice,
  label,
  style,
}: {
  workspaceId: string;
  itemId: string;
  actionItemId: string;
  choice: "primary" | "secondary";
  label: string;
  style: ButtonStyle;
}) {
  const tone =
    style === "danger"
      ? "border-coral/40 bg-coral/10 text-coral hover:bg-coral/20"
      : "border-white/15 bg-white/[0.04] text-white/85 hover:bg-white/[0.08]";
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/disposition`}
      method="POST"
      className="contents"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action" value="resolve" />
      <input type="hidden" name="action_item_id" value={actionItemId} />
      <input type="hidden" name="choice" value={choice} />
      <button
        type="submit"
        className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition ${tone}`}
      >
        {label}
      </button>
    </form>
  );
}

function PrimaryActionForm({
  workspaceId,
  itemId,
  spec,
}: {
  workspaceId: string;
  itemId: string;
  spec: ButtonSpec;
}) {
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/disposition`}
      method="POST"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action" value={spec.action} />
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
      >
        {spec.label}
      </button>
    </form>
  );
}

function SecondaryActionForm({
  workspaceId,
  itemId,
  spec,
}: {
  workspaceId: string;
  itemId: string;
  spec: ButtonSpec;
}) {
  const tone =
    spec.style === "danger"
      ? "border-coral/40 bg-coral/10 text-coral hover:bg-coral/20"
      : "border-white/15 bg-white/[0.04] text-white/85 hover:bg-white/[0.08]";
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/disposition`}
      method="POST"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action" value={spec.action} />
      <button
        type="submit"
        className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition ${tone}`}
      >
        {spec.label}
      </button>
    </form>
  );
}

function AnswerForm({
  workspaceId,
  itemId,
}: {
  workspaceId: string;
  itemId: string;
}) {
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/disposition`}
      method="POST"
      className="space-y-2"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action" value="answer" />
      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
          Your answer
        </span>
        <textarea
          name="answer"
          rows={4}
          required
          placeholder="Reply to the agent's question…"
          className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
        />
      </label>
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
      >
        Answer & resolve
      </button>
    </form>
  );
}

function ResolvedCard({ detail }: { detail: InboxItemDetail }) {
  const tone = detail.status === "dismissed" ? "neutral" : "ok";
  // Reach into the audit trail for the user that closed this item;
  // the InboxItem row itself doesn't denormalise the resolver.
  const closingEvent = [...(detail.events ?? [])]
    .reverse()
    .find((e) => e.action === "resolved" || e.action === "dismissed");
  return (
    <Card>
      <CardHeader title="Closed" />
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={tone}>
          {detail.status === "dismissed" ? "Dismissed" : "Resolved"}
        </Badge>
        {detail.resolution && (
          <span className="text-xs text-white/75">
            Resolution:{" "}
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
              {detail.resolution}
            </code>
          </span>
        )}
      </div>
      {closingEvent ? (
        <p className="mt-3 text-xs text-white/55">
          {detail.status === "dismissed" ? "Dismissed" : "Resolved"} by{" "}
          <span className="text-white/85">
            {actorLabel(closingEvent, [])}
          </span>{" "}
          on <span className="text-white/85">{absolute(detail.resolved_at ?? closingEvent.created_at)}</span>
          .
        </p>
      ) : detail.resolved_at ? (
        <p className="mt-3 text-xs text-white/55">
          Closed on{" "}
          <span className="text-white/85">{absolute(detail.resolved_at)}</span>
          .
        </p>
      ) : null}
    </Card>
  );
}

function SnoozeForm({
  workspaceId,
  itemId,
}: {
  workspaceId: string;
  itemId: string;
}) {
  // Deterministic preset: tomorrow at 09:00 UTC. We deliberately
  // pick a fixed hour rather than "now + 24h" so tests + SSR
  // snapshots are stable and so the dropdown reads as "9am
  // tomorrow" rather than "8:42pm tomorrow if you happened to load
  // the page at 8:42pm". The operator's browser will reinterpret
  // the value as their local clock when the input renders.
  const tomorrow = new Date();
  tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
  tomorrow.setUTCHours(9, 0, 0, 0);
  const yyyy = tomorrow.getUTCFullYear();
  const mm = String(tomorrow.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(tomorrow.getUTCDate()).padStart(2, "0");
  const presetValue = `${yyyy}-${mm}-${dd}T09:00`;

  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/snooze`}
      method="POST"
      className="mt-5 grid gap-2 border-t border-white/10 pt-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
          Snooze until
        </span>
        <input
          type="datetime-local"
          name="snoozed_until"
          defaultValue={presetValue}
          required
          className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
        />
      </label>
      <button
        type="submit"
        className="inline-flex items-center justify-center rounded-full border border-white/15 bg-white/[0.06] px-4 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.1]"
      >
        Snooze
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Source ticket card
// ---------------------------------------------------------------------------

/**
 * Render a snapshot of the tracker ticket the agent's question/blocker
 * refers to. The backend stamps this onto ``payload.source_ticket`` at
 * agent-finish time (clarifications + blockers carrying a ticket_ref);
 * if the snapshot is missing — older items, no tracker bound, fetch
 * failed — fall back to a minimal "ref only" pill so the operator at
 * least sees which ticket is meant.
 */
function SourceTicketCard({ detail }: { detail: InboxItemDetail }) {
  const payload = (detail.payload ?? {}) as Record<string, unknown>;
  const snap =
    payload.source_ticket && typeof payload.source_ticket === "object"
      ? (payload.source_ticket as Record<string, unknown>)
      : null;
  const ticketRef =
    typeof payload.ticket_ref === "string" && payload.ticket_ref
      ? payload.ticket_ref
      : null;
  const canDiscuss =
    detail.status === "new" &&
    (detail.type === "clarification" || detail.type === "blocker");

  // Nothing tying this row to a ticket — don't render anything (unless
  // it's still actionable via Navigator chat).
  if (!snap && !ticketRef && !canDiscuss) return null;

  const title = typeof snap?.title === "string" ? snap.title : null;
  const description =
    typeof snap?.description === "string" ? snap.description : null;
  const url = typeof snap?.url === "string" ? snap.url : null;
  const state = typeof snap?.state === "string" ? snap.state : null;
  const labels = Array.isArray(snap?.labels)
    ? (snap?.labels as unknown[]).filter(
        (l): l is string => typeof l === "string",
      )
    : [];
  const refDisplay =
    (typeof snap?.ticket_ref === "string" && snap.ticket_ref) ||
    ticketRef ||
    "";

  return (
    <Card>
      <CardHeader
        title="Source ticket"
        subtitle={`The ticket the agent's ${detail.type === "blocker" ? "blocker" : "question"} refers to.`}
      />
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <code className="rounded bg-white/[0.08] px-2 py-1 font-mono text-aqua/95">
            {refDisplay}
          </code>
          {state && (
            <span className="rounded-full border border-white/15 px-2 py-0.5 text-white/75">
              {state}
            </span>
          )}
          {labels.map((lbl) => (
            <span
              key={lbl}
              className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-white/65"
            >
              {lbl}
            </span>
          ))}
          {url && (
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="ml-auto text-aqua hover:underline"
            >
              Open in tracker ↗
            </a>
          )}
        </div>
        {title && (
          <h2 className="font-display text-base font-bold text-white">
            {title}
          </h2>
        )}
        {description ? (
          <MarkdownBlock>{description}</MarkdownBlock>
        ) : title || snap ? null : (
          <p className="text-xs text-white/55">
            No ticket snapshot — older inbox row, or the tracker
            wasn&apos;t reachable when the agent finished.
          </p>
        )}
        {canDiscuss && (
          <form
            action={`/api/inbox/${encodeURIComponent(detail.id)}/discuss`}
            method="POST"
            className="pt-1"
          >
            <input type="hidden" name="ws" value={detail.workspace_id} />
            <button
              type="submit"
              className="rounded-lg bg-aqua/90 px-3 py-2 text-xs font-semibold text-black shadow-sm hover:bg-aqua"
            >
              💬 Discuss with Navigator
            </button>
            <span className="ml-3 text-xs text-white/55">
              Opens a fresh chat seeded with this ticket + the agent&apos;s
              {detail.type === "blocker" ? " blocker" : " question"}.
            </span>
          </form>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Events timeline
// ---------------------------------------------------------------------------

function EventsCard({
  events,
  members,
}: {
  events: InboxItemEvent[];
  members: ApiMember[];
}) {
  // Hide the card entirely when the only event is the intake "created"
  // heartbeat — adds noise without telling the operator anything new.
  const meaningful = events.filter(
    (e) => !(e.action === "created" && e.actor_kind === "system"),
  );
  if (meaningful.length === 0) return null;
  return (
    <Card>
      <CardHeader
        title="Activity"
        subtitle="Audit trail in chronological order."
      />
      <ol className="space-y-3">
        {meaningful.map((event) => (
          <EventRow key={event.id} event={event} members={members} />
        ))}
      </ol>
    </Card>
  );
}

function EventRow({
  event,
  members,
}: {
  event: InboxItemEvent;
  members: ApiMember[];
}) {
  const summary = summarizeEventPayload(event, members);
  return (
    <li className="flex gap-3">
      <span
        className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full bg-current"
        aria-hidden
        style={{ color: actionColor(event.action) }}
      />
      <div className="min-w-0 flex-1">
        <div className="text-xs text-white/85">
          <strong className="font-semibold text-white">
            {actorLabel(event, members)}
          </strong>{" "}
          <span className="text-white/55">{actionPhrase(event.action)}</span>
          <span className="ml-2 text-[11px] text-white/45">
            · {formatRelative(event.created_at)}
          </span>
        </div>
        {summary && (
          <p className="mt-0.5 text-[11px] leading-snug text-white/55">
            {summary}
          </p>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Comment form
// ---------------------------------------------------------------------------

function CommentCard({
  workspaceId,
  itemId,
}: {
  workspaceId: string;
  itemId: string;
}) {
  return (
    <Card>
      <CardHeader title="Add a note" />
      <form
        action={`/api/inbox/${encodeURIComponent(itemId)}/comment`}
        method="POST"
        className="space-y-2"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="id" value={itemId} />
        <textarea
          name="body"
          rows={3}
          required
          placeholder="Leave context for the next person picking this up…"
          className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
        />
        <button
          type="submit"
          className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.06] px-4 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.1]"
        >
          Post comment
        </button>
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Sidebar: owner card (with reassign forms)
// ---------------------------------------------------------------------------

function OwnerCard({
  detail,
  workspaceId,
  members,
}: {
  detail: InboxItemDetail;
  workspaceId: string;
  members: ApiMember[];
}) {
  return (
    <Card>
      <CardHeader title="Owner" />
      {detail.owner ? (
        <div className="mb-4 flex items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-aqua via-lilac to-coral text-[11px] font-bold text-ink">
            {initialsFromSource(
              detail.owner.display_name?.trim() || detail.owner.email,
            )}
          </span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-white">
              {detail.owner.display_name || detail.owner.email}
            </div>
            <div className="truncate text-[11px] text-white/55">
              {detail.owner.email}
            </div>
          </div>
        </div>
      ) : (
        <p className="mb-4 text-xs text-white/65">
          Currently <strong className="text-white">unassigned</strong>. Pick
          a member or re-resolve via a routing handle below.
        </p>
      )}

      <div className="space-y-3 border-t border-white/10 pt-3">
        <ReassignByMemberForm
          workspaceId={workspaceId}
          itemId={detail.id}
          members={members}
        />
        <ReassignByHandleForm
          workspaceId={workspaceId}
          itemId={detail.id}
        />
      </div>
    </Card>
  );
}

function ReassignByMemberForm({
  workspaceId,
  itemId,
  members,
}: {
  workspaceId: string;
  itemId: string;
  members: ApiMember[];
}) {
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/reassign`}
      method="POST"
      className="space-y-2"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
          Reassign to member
        </span>
        <select
          name="user_id"
          defaultValue=""
          className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
        >
          <option value="">— pick a member —</option>
          {members.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {(m.display_name?.trim() || m.email) +
                (m.role ? ` · ${m.role}` : "")}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        className="w-full rounded-full border border-white/15 bg-white/[0.06] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.1]"
      >
        Reassign
      </button>
    </form>
  );
}

function ReassignByHandleForm({
  workspaceId,
  itemId,
}: {
  workspaceId: string;
  itemId: string;
}) {
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/reassign`}
      method="POST"
      className="space-y-2"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/55">
          Or re-route via handle
        </span>
        <input
          type="text"
          name="handle"
          placeholder="e.g. secops"
          className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 font-mono text-xs text-white outline-none focus:border-aqua/40"
        />
      </label>
      <button
        type="submit"
        className="w-full rounded-full border border-white/15 bg-white/[0.06] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.1]"
      >
        Resolve & reassign
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Sidebar: source card
// ---------------------------------------------------------------------------

function SourceCard({ detail }: { detail: InboxItemDetail }) {
  const hasAnything = detail.play_key || detail.run_id;
  if (!hasAnything) return null;
  return (
    <Card>
      <CardHeader title="Source" />
      <dl className="space-y-2 text-xs">
        {detail.play_key && (
          <Row label="Play">
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5 text-white/85">
              {detail.play_key}
            </code>
          </Row>
        )}
        {detail.run_id && (
          <Row label="Run">
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5 text-white/85">
              {detail.run_id.slice(0, 8)}
            </code>
          </Row>
        )}
        <Row label="Created">
          <span className="text-white/85">{absolute(detail.created_at)}</span>
        </Row>
      </dl>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-[10px] font-semibold uppercase tracking-widest text-white/45">
        {label}
      </dt>
      <dd className="min-w-0 text-right text-xs text-white/85">{children}</dd>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "new"
      ? "info"
      : status === "snoozed"
        ? "warn"
        : status === "resolved"
          ? "ok"
          : "neutral";
  return <Badge tone={tone}>{status}</Badge>;
}

function actorLabel(event: InboxItemEvent, members: ApiMember[]): string {
  if (event.actor_kind === "system") return "system";
  if (event.actor_kind === "agent") return "agent";
  if (event.actor_user_id) {
    const member = members.find(
      (m) => m.user_id === event.actor_user_id,
    );
    if (member) return member.display_name || member.email;
  }
  return "someone";
}

function actionPhrase(action: string): string {
  switch (action) {
    case "created":
      return "created the item";
    case "assigned":
      return "assigned the item";
    case "reassigned":
      return "reassigned the item";
    case "snoozed":
      return "snoozed the item";
    case "unsnoozed":
      return "woke the item up";
    case "resolved":
      return "resolved the item";
    case "dismissed":
      return "dismissed the item";
    case "commented":
      return "commented";
    default:
      // Forward-compatible: P2-09's escalation events (or anything
      // else the backend may add) render as "did <verb>" without
      // breaking the row.
      return `did ${action.replace(/_/g, " ")}`;
  }
}

function actionColor(action: string): string {
  switch (action) {
    case "resolved":
      return "rgba(110, 231, 183, 0.85)";
    case "dismissed":
      return "rgba(248, 113, 113, 0.7)";
    case "snoozed":
    case "unsnoozed":
      return "rgba(250, 204, 21, 0.85)";
    case "reassigned":
      return "rgba(167, 139, 250, 0.85)";
    case "commented":
      return "rgba(56, 189, 248, 0.85)";
    case "created":
      return "rgba(255, 255, 255, 0.55)";
    default:
      return "rgba(255, 255, 255, 0.45)";
  }
}

function summarizeEventPayload(
  event: InboxItemEvent,
  members: ApiMember[],
): string | null {
  const payload = event.payload ?? {};
  if (event.action === "commented") {
    const body = typeof payload.body === "string" ? payload.body : "";
    return body || null;
  }
  if (event.action === "resolved" || event.action === "dismissed") {
    const disposition =
      typeof payload.disposition === "string" ? payload.disposition : null;
    const resolution =
      typeof payload.resolution === "string" ? payload.resolution : null;
    if (disposition && resolution) {
      return `via ${disposition} → ${resolution}`;
    }
    if (disposition) return `via ${disposition}`;
    if (resolution) return resolution;
    return null;
  }
  if (event.action === "reassigned") {
    const oldOwner = lookupMember(payload.old_owner_user_id, members);
    const newOwner = lookupMember(payload.new_owner_user_id, members);
    const handle =
      typeof payload.handle === "string" ? payload.handle : null;
    if (oldOwner && newOwner) {
      return `${oldOwner} → ${newOwner}${handle ? ` (handle ${handle})` : ""}`;
    }
    if (newOwner) {
      return `now ${newOwner}${handle ? ` (handle ${handle})` : ""}`;
    }
    if (handle) return `via handle ${handle}`;
    return null;
  }
  if (event.action === "snoozed") {
    const until =
      typeof payload.snoozed_until === "string" ? payload.snoozed_until : null;
    return until ? `until ${absolute(until)}` : null;
  }
  return null;
}

function lookupMember(
  raw: unknown,
  members: ApiMember[],
): string | null {
  if (typeof raw !== "string" || raw.length === 0) return null;
  const found = members.find((m) => m.user_id === raw);
  if (found) return found.display_name || found.email;
  return raw.slice(0, 8);
}

/**
 * "5m ago" / "yesterday" / "Apr 23" — deterministic given the input
 * ISO + Date.now(). Pure function so tests can pin Date.now() for
 * SSR snapshots.
 */
function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return iso;
  const diffMs = Date.now() - then;
  const sec = Math.round(diffMs / 1000);
  if (sec < 30) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d ago`;
  // ≥ 1w → fall back to a stable absolute label so the timeline
  // doesn't read as "21d ago" / "63d ago" forever.
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function absolute(iso: string): string {
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function initialsFromSource(source: string): string {
  const parts = source.split(/[\s@.]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (parts[0]?.slice(0, 2) ?? "??").toUpperCase();
}

function errorMessage(code: string): string {
  switch (code) {
    case "bad_input":
      return "Missing or malformed form fields. Try again.";
    case "forbidden":
      return "Only the assigned owner or a workspace admin can apply this disposition.";
    case "not_found":
      return "Item no longer exists. Refresh the inbox to see the current state.";
    case "validation_failed":
      return "The backend rejected this disposition (likely a state-machine violation). Try again or refresh.";
    case "state_invalid":
      return "Item is in the wrong state for this action — refresh to see the latest status.";
    case "snooze_too_far":
      return "Snoozes are capped at 30 days. Pick a closer date or reassign instead.";
    case "snooze_in_past":
      return "Snooze deadline must be in the future.";
    case "handle_unresolved":
      return "That handle didn't resolve to any user. Check workspace routing rules.";
    case "session_expired":
      return "Your session expired — please sign in again.";
    case "api_unavailable":
      return "Backend is unreachable. Try again in a moment.";
    case "unknown":
      return "Something went wrong. Try again or refresh.";
    default:
      if (code.startsWith("http_")) {
        return `Backend returned ${code.slice(5)}.`;
      }
      return `Couldn't apply the change (${code}). Try again or refresh.`;
  }
}

// ---------------------------------------------------------------------------
// MockView — single fake approval item with deterministic data
// ---------------------------------------------------------------------------

const MOCK_REFERENCE_ISO = "2026-04-24T15:30:00.000Z";

function mockDetail(id: string): InboxItemDetail {
  const ref = new Date(MOCK_REFERENCE_ISO);
  const minus = (mins: number) =>
    new Date(ref.getTime() - mins * 60_000).toISOString();
  return {
    id,
    workspace_id: "ws_mock_demo",
    repo_id: "repo_mock_helio",
    type: "approval",
    status: "new",
    title: "Approve schema migration to v17",
    summary:
      "Migration drops the legacy `audit_v1` table and adds three new indexes on inbox_items. The play paused before applying.",
    intake_handle: "release_manager",
    intake_reason: "routing_rule:release_manager",
    owner: {
      user_id: "user_mock_dk",
      email: "denis@helio.dev",
      display_name: "Denis K.",
    },
    play_key: "release/migrate.v17",
    run_id: "run_abcdef0123456789",
    created_at: minus(60 * 6),
    due_at: null,
    snoozed_until: null,
    resolved_at: null,
    resolution: null,
    payload: {
      migration_id: "v17",
      drops: ["audit_v1"],
      adds: [
        "inbox_items_status_owner_idx",
        "inbox_items_workspace_created_idx",
        "inbox_items_play_key_idx",
      ],
      requires_approval: true,
    },
    source_table: "pipeline_runs",
    source_id: "src_run_v17_abc12345",
    events: [
      {
        id: "evt_mock_created",
        actor_kind: "system",
        actor_user_id: null,
        action: "created",
        payload: { intake_handle: "release_manager" },
        created_at: minus(60 * 6),
      },
      {
        id: "evt_mock_assigned",
        actor_kind: "system",
        actor_user_id: null,
        action: "assigned",
        payload: { new_owner_user_id: "user_mock_dk" },
        created_at: minus(60 * 6 - 1),
      },
      {
        id: "evt_mock_comment",
        actor_kind: "user",
        actor_user_id: "user_mock_mt",
        action: "commented",
        payload: {
          body: "Looks safe — verified the audit_v1 retention copy in storage.",
        },
        created_at: minus(45),
      },
    ],
  };
}

function MockView({
  reason,
  errorCode,
  id,
}: {
  reason: string;
  errorCode: string | null;
  id: string;
}) {
  const detail = mockDetail(id);
  const meta = INBOX_TYPE_META[detail.type as InboxType];
  return (
    <>
      <PageHeader kicker={meta?.label ?? detail.type} title={detail.title} />
      <PageBody>
        <ApiUnavailable scope="inbox" details={reason} />
        {errorCode && (
          <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
            {errorMessage(errorCode)}
          </div>
        )}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="space-y-4">
            <HeaderCard detail={detail} />
            <SourceTicketCard detail={detail} />
            <DispositionCard detail={detail} workspaceId={detail.workspace_id} />
            <EventsCard events={detail.events} members={[]} />
            <CommentCard
              workspaceId={detail.workspace_id}
              itemId={detail.id}
            />
          </div>
          <aside className="space-y-4">
            <OwnerCard
              detail={detail}
              workspaceId={detail.workspace_id}
              members={[]}
            />
            <SourceCard detail={detail} />
          </aside>
        </div>
      </PageBody>
    </>
  );
}
