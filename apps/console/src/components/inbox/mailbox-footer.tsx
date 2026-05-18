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
 *
 * Closed items (``resolved`` / ``dismissed``) collapse to a one-line
 * confirmation row instead of the action surface.
 */

import {
  inboxFooterKind,
  reportActionItemDecisions,
  reportActionItems,
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

  const kind = inboxFooterKind(detail.type as InboxType, detail.payload);
  if (kind === "report_actions") {
    return (
      <ReportActionItemsFooter
        workspaceId={workspaceId}
        itemId={detail.id}
        payload={detail.payload}
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
    <div className="flex flex-wrap items-center gap-2">
      <PrimaryForm
        workspaceId={workspaceId}
        itemId={detail.id}
        action={decision.primary.action}
        label={decision.primary.label}
      />
      {decision.secondary.map((spec) => (
        <SecondaryForm
          key={spec.action + spec.label}
          workspaceId={workspaceId}
          itemId={detail.id}
          action={spec.action}
          label={spec.label}
          style={spec.style}
        />
      ))}
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
      <div className="flex items-center gap-2">
        <button
          type="submit"
          className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-1.5 text-sm font-bold text-ink shadow-glow transition hover:brightness-110"
        >
          Send
        </button>
        <DismissForm workspaceId={workspaceId} itemId={itemId} />
      </div>
    </form>
  );
}

function ReportActionItemsFooter({
  workspaceId,
  itemId,
  payload,
}: {
  workspaceId: string;
  itemId: string;
  payload: Record<string, unknown>;
}) {
  const items = reportActionItems(payload);
  const decided = reportActionItemDecisions(payload);
  const pending = items.filter((item) => !decided[item.id]);

  if (pending.length === 0) {
    return (
      <p className="text-xs text-white/55">
        All recommendations decided — refresh to see the closed state.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {pending.map((item) => (
        <ReportActionItemRow
          key={item.id}
          workspaceId={workspaceId}
          itemId={itemId}
          item={item}
        />
      ))}
    </div>
  );
}

function ReportActionItemRow({
  workspaceId,
  itemId,
  item,
}: {
  workspaceId: string;
  itemId: string;
  item: { id: string; prompt: string; primary: string; secondary: string };
}) {
  return (
    <div className="rounded border border-white/10 bg-white/[0.03] p-3">
      <p className="text-sm text-white/85">{item.prompt}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <ActionItemChoiceForm
          workspaceId={workspaceId}
          itemId={itemId}
          actionItemId={item.id}
          choice="primary"
          label={item.primary}
          tone="ghost"
        />
        <ActionItemChoiceForm
          workspaceId={workspaceId}
          itemId={itemId}
          actionItemId={item.id}
          choice="secondary"
          label={item.secondary}
          tone="danger"
        />
      </div>
    </div>
  );
}

function ActionItemChoiceForm({
  workspaceId,
  itemId,
  actionItemId,
  choice,
  label,
  tone,
}: {
  workspaceId: string;
  itemId: string;
  actionItemId: string;
  choice: "primary" | "secondary";
  label: string;
  tone: "ghost" | "danger";
}) {
  const className =
    tone === "danger"
      ? "inline-flex items-center gap-1.5 rounded-full border border-coral/40 bg-coral/10 px-3 py-1.5 text-xs font-semibold text-coral transition hover:bg-coral/20"
      : "inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.08]";
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
      <input type="hidden" name="return_to" value="/inbox" />
      <button type="submit" className={className}>
        {label}
      </button>
    </form>
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
