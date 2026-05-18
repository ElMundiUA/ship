/**
 * Type-aware footer for the mailbox preview pane.
 *
 *   - ``acknowledge`` — single Acknowledge button (reports / read-only
 *     digests). Posts ``action=resolve`` which the backend maps to
 *     ``resolution=acknowledged``.
 *   - ``reply`` — quick-reply textarea + Send button (clarifications).
 *     Posts ``action=answer`` with the operator's text.
 *   - ``decision`` — primary + secondary disposition buttons. The verb
 *     set per type is the same as the bigger ``/inbox/[id]`` page.
 *   - ``checklist`` — one row per ``payload.action_items`` entry with
 *     prompt + primary/secondary choices (daily digest shape).
 *
 * Closed items (``resolved`` / ``dismissed``) collapse to a one-line
 * confirmation row instead of the action surface.
 */

import { ButtonGhost, ButtonPrimary } from "@/components/ui";
import {
  inboxActionItems,
  inboxFooterKind,
  type InboxActionItem,
  type InboxItemDetail,
  type InboxType,
} from "@/lib/inbox-types";

type ActionVerb =
  | "resolve"
  | "dismiss"
  | "approve"
  | "reject"
  | "answer"
  | "accept"
  | "retry"
  | "acknowledge";

type ButtonStyle = "primary" | "secondary" | "danger";

type Decision = {
  primary: { action: ActionVerb; label: string };
  secondary: { action: ActionVerb; label: string; style: ButtonStyle }[];
};

const DECISIONS: Partial<Record<InboxType, Decision>> = {
  approval: {
    primary: { action: "approve", label: "Approve" },
    secondary: [
      { action: "reject", label: "Reject", style: "danger" },
      { action: "dismiss", label: "Dismiss", style: "secondary" },
    ],
  },
  improvement: {
    primary: { action: "accept", label: "Accept" },
    secondary: [
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
  failure: {
    primary: { action: "retry", label: "Retry" },
    secondary: [
      { action: "resolve", label: "Acknowledge", style: "secondary" },
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
  exception: {
    primary: { action: "acknowledge", label: "Acknowledge" },
    secondary: [
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
  stuck: {
    primary: { action: "resolve", label: "Mark addressed" },
    secondary: [
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
  blocker: {
    primary: { action: "resolve", label: "Mark handled" },
    secondary: [
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
};

export function MailboxFooter({
  detail,
  workspaceId,
}: {
  detail: InboxItemDetail;
  workspaceId: string;
}) {
  if (detail.status === "resolved" || detail.status === "dismissed") {
    return (
      <p className="text-xs text-white/55">
        {detail.status === "dismissed" ? "Dismissed" : "Resolved"}
        {detail.resolution ? (
          <>
            {" "}
            ·{" "}
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
              {detail.resolution}
            </code>
          </>
        ) : null}
      </p>
    );
  }

  const kind = inboxFooterKind(detail.type as InboxType, {
    status: detail.status,
    payload: detail.payload,
  });
  if (kind === "checklist") {
    return (
      <ChecklistFooter
        workspaceId={workspaceId}
        itemId={detail.id}
        items={inboxActionItems(detail.payload)}
      />
    );
  }
  if (kind === "acknowledge") {
    return (
      <PrimaryForm
        workspaceId={workspaceId}
        itemId={detail.id}
        action="resolve"
        label="Acknowledge"
      />
    );
  }
  if (kind === "reply") {
    return (
      <ReplyForm workspaceId={workspaceId} itemId={detail.id} />
    );
  }

  const decision = DECISIONS[detail.type as InboxType];
  if (!decision) {
    return (
      <PrimaryForm
        workspaceId={workspaceId}
        itemId={detail.id}
        action="resolve"
        label="Resolve"
      />
    );
  }
  return (
    <DecisionFooter
      workspaceId={workspaceId}
      itemId={detail.id}
      decision={decision}
    />
  );
}

function DecisionFooter({
  workspaceId,
  itemId,
  decision,
}: {
  workspaceId: string;
  itemId: string;
  decision: Decision;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <PrimaryForm
        workspaceId={workspaceId}
        itemId={itemId}
        action={decision.primary.action}
        label={decision.primary.label}
      />
      {decision.secondary.map((spec) => (
        <SecondaryForm
          key={spec.action + spec.label}
          workspaceId={workspaceId}
          itemId={itemId}
          action={spec.action}
          label={spec.label}
          style={spec.style}
        />
      ))}
    </div>
  );
}

function ChecklistFooter({
  workspaceId,
  itemId,
  items,
}: {
  workspaceId: string;
  itemId: string;
  items: InboxActionItem[];
}) {
  return (
    <ul className="space-y-4">
      {items.map((row) => (
        <li key={row.id} className="space-y-2">
          <p className="text-sm text-white/85">{row.prompt}</p>
          <ChecklistRow workspaceId={workspaceId} itemId={itemId} row={row} />
        </li>
      ))}
    </ul>
  );
}

function ChecklistRow({
  workspaceId,
  itemId,
  row,
}: {
  workspaceId: string;
  itemId: string;
  row: InboxActionItem;
}) {
  const actionPath = `/api/inbox/${encodeURIComponent(itemId)}/disposition`;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <form action={actionPath} method="POST" className="contents">
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="action" value="resolve" />
        <input type="hidden" name="return_to" value="/inbox" />
        <input type="hidden" name="action_item_id" value={row.id} />
        <input type="hidden" name="choice" value={row.primary.choice} />
        <ButtonPrimary type="submit">{row.primary.label}</ButtonPrimary>
      </form>
      <form action={actionPath} method="POST" className="contents">
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="action" value="dismiss" />
        <input type="hidden" name="return_to" value="/inbox" />
        <input type="hidden" name="action_item_id" value={row.id} />
        <input type="hidden" name="choice" value={row.secondary.choice} />
        <ButtonGhost type="submit">{row.secondary.label}</ButtonGhost>
      </form>
    </div>
  );
}

function PrimaryForm({
  workspaceId,
  itemId,
  action,
  label,
}: {
  workspaceId: string;
  itemId: string;
  action: ActionVerb;
  label: string;
}) {
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/disposition`}
      method="POST"
      className="contents"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action" value={action} />
      <input type="hidden" name="return_to" value="/inbox" />
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-1.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
      >
        {label}
      </button>
    </form>
  );
}

function SecondaryForm({
  workspaceId,
  itemId,
  action,
  label,
  style,
}: {
  workspaceId: string;
  itemId: string;
  action: ActionVerb;
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
      <input type="hidden" name="action" value={action} />
      <input type="hidden" name="return_to" value="/inbox" />
      <button
        type="submit"
        className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition ${tone}`}
      >
        {label}
      </button>
    </form>
  );
}

function ReplyForm({
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
      <input type="hidden" name="return_to" value="/inbox" />
      <textarea
        name="answer"
        rows={3}
        required
        placeholder="Reply to the agent…"
        className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
      />
      <ReplyActions workspaceId={workspaceId} itemId={itemId} />
    </form>
  );
}

function ReplyActions({
  workspaceId,
  itemId,
}: {
  workspaceId: string;
  itemId: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-1.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
      >
        Send
      </button>
      <DismissForm workspaceId={workspaceId} itemId={itemId} />
    </div>
  );
}

function DismissForm({
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
      className="contents"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action" value="dismiss" />
      <input type="hidden" name="return_to" value="/inbox" />
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.08]"
      >
        Dismiss
      </button>
    </form>
  );
}
