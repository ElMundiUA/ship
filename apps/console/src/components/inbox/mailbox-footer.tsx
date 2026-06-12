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

import { SnoozeForm } from "@/components/inbox/snooze-form";
import { ButtonDanger, ButtonGhost } from "@/components/ui";
import {
  inboxFooterKind,
  parseChecklistActionItems,
  reportActionItemDecisions,
  reportActionItems,
  type InboxChecklistActionItem,
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
  returnTo = "/",
}: {
  detail: InboxItemDetail;
  workspaceId: string;
  /** Bounce target after disposition (the `/approve/{id}` page passes itself). */
  returnTo?: string;
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

  const kind = inboxFooterKind(detail);
  if (kind === "checklist") {
    return (
      <ChecklistFooter
        workspaceId={workspaceId}
        itemId={detail.id}
        items={parseChecklistActionItems(detail.payload)}
        returnTo={returnTo}
      />
    );
  }
  if (kind === "report_actions") {
    return (
      <ReportActionItemsFooter
        workspaceId={workspaceId}
        itemId={detail.id}
        returnTo={returnTo}
        payload={detail.payload}
      />
    );
  }
  if (kind === "acknowledge") {
    // Reports (daily digest, learning capture, retro) only had a
    // single Acknowledge button — operators routinely wanted a
    // "Snooze 24h" without opening the legacy datetime-local picker
    // ("come back tomorrow"), or a "Dismiss" for digests that
    // arrived during a freeze. Surface the three together so the
    // mailbox preview matches what a Decision-UI letter offers.
    return (
      <div className="flex flex-wrap items-center gap-2">
        <PrimaryForm
          workspaceId={workspaceId}
          itemId={detail.id}
          action="resolve"
          label="Acknowledge"
          returnTo={returnTo}
        />
        <SnoozeForm
          workspaceId={workspaceId}
          itemId={detail.id}
          hours={24}
          label="Snooze 24h"
        />
        <SecondaryForm
          workspaceId={workspaceId}
          itemId={detail.id}
          action="dismiss"
          label="Dismiss"
          style="danger"
          returnTo={returnTo}
        />
      </div>
    );
  }
  if (kind === "reply") {
    return (
      <ReplyForm
        workspaceId={workspaceId}
        itemId={detail.id}
        returnTo={returnTo}
      />
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
        returnTo={returnTo}
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
        returnTo={returnTo}
      />
      {decision.secondary.map((spec) => (
        <SecondaryForm
          key={spec.action + spec.label}
          workspaceId={workspaceId}
          itemId={detail.id}
          action={spec.action}
          label={spec.label}
          style={spec.style}
          returnTo={returnTo}
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
  returnTo,
}: {
  workspaceId: string;
  itemId: string;
  action: ActionVerb;
  label: string;
  returnTo: string;
}) {
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/disposition`}
      method="POST"
      className="contents"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action" value={action} />
      <input type="hidden" name="return_to" value={returnTo} />
      <button
        type="submit"
        className="inline-flex items-center justify-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-semibold text-ink shadow-glow transition hover:brightness-110 active:scale-[0.99]"
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
  returnTo,
}: {
  workspaceId: string;
  itemId: string;
  action: ActionVerb;
  label: string;
  style: ButtonStyle;
  returnTo: string;
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
      <input type="hidden" name="return_to" value={returnTo} />
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
  returnTo,
}: {
  workspaceId: string;
  itemId: string;
  returnTo: string;
}) {
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/disposition`}
      method="POST"
      className="space-y-2"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action" value="answer" />
      <input type="hidden" name="return_to" value={returnTo} />
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
          className="inline-flex items-center justify-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-5 py-2 text-sm font-semibold text-ink shadow-glow transition hover:brightness-110 active:scale-[0.99]"
        >
          Send
        </button>
        <DismissForm
          workspaceId={workspaceId}
          itemId={itemId}
          returnTo={returnTo}
        />
      </div>
    </form>
  );
}

function ReportActionItemsFooter({
  workspaceId,
  itemId,
  payload,
  returnTo,
}: {
  workspaceId: string;
  itemId: string;
  payload: Record<string, unknown>;
  returnTo: string;
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
          returnTo={returnTo}
        />
      ))}
    </div>
  );
}

function ReportActionItemRow({
  workspaceId,
  itemId,
  item,
  returnTo,
}: {
  workspaceId: string;
  itemId: string;
  item: { id: string; hint: string; label: string; secondary_label: string };
  returnTo: string;
}) {
  return (
    <div className="rounded border border-white/10 bg-white/[0.03] p-3">
      <p className="text-sm text-white/85">{item.hint}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <ActionItemChoiceForm
          workspaceId={workspaceId}
          itemId={itemId}
          actionItemId={item.id}
          choice="primary"
          label={item.label}
          tone="ghost"
          returnTo={returnTo}
        />
        <ActionItemChoiceForm
          workspaceId={workspaceId}
          itemId={itemId}
          actionItemId={item.id}
          choice="secondary"
          label={item.secondary_label}
          tone="danger"
          returnTo={returnTo}
        />
      </div>
    </div>
  );
}

function ChecklistFooter({
  workspaceId,
  itemId,
  items,
  returnTo,
}: {
  workspaceId: string;
  itemId: string;
  items: InboxChecklistActionItem[];
  returnTo: string;
}) {
  return (
    <ul className="space-y-3" data-testid="inbox-checklist-footer">
      {items.map((item) => (
        <li key={item.id} className="space-y-2">
          <p className="text-sm text-white/85">{item.prompt}</p>
          <div className="flex flex-wrap items-center gap-2">
            <ChecklistChoiceForm
              workspaceId={workspaceId}
              itemId={itemId}
              actionItemId={item.id}
              choice={item.primary.choice}
              label={item.primary.label}
              returnTo={returnTo}
              variant="primary"
            />
            <ChecklistChoiceForm
              workspaceId={workspaceId}
              itemId={itemId}
              actionItemId={item.id}
              choice={item.secondary.choice}
              label={item.secondary.label}
              returnTo={returnTo}
              variant="secondary"
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

function ChecklistChoiceForm({
  workspaceId,
  itemId,
  actionItemId,
  choice,
  label,
  returnTo,
  variant,
}: {
  workspaceId: string;
  itemId: string;
  actionItemId: string;
  choice: string;
  label: string;
  returnTo: string;
  variant: "primary" | "secondary";
}) {
  const Button = variant === "primary" ? ButtonGhost : ButtonDanger;
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
      <input type="hidden" name="return_to" value={returnTo} />
      <Button type="submit">{label}</Button>
    </form>
  );
}

function ActionItemChoiceForm({
  workspaceId,
  itemId,
  actionItemId,
  choice,
  label,
  tone,
  returnTo,
}: {
  workspaceId: string;
  itemId: string;
  actionItemId: string;
  choice: "primary" | "secondary";
  label: string;
  tone: "ghost" | "danger";
  returnTo: string;
}) {
  const className =
    tone === "danger"
      ? "inline-flex items-center gap-1.5 rounded-full border border-coral/40 bg-coral/10 px-3 py-1.5 text-xs font-semibold text-coral transition hover:bg-coral/20"
      : "inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.08]";
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/decide`}
      method="POST"
      className="contents"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action_item_id" value={actionItemId} />
      <input type="hidden" name="choice" value={choice} />
      <input type="hidden" name="return_to" value={returnTo} />
      <button type="submit" className={className}>
        {label}
      </button>
    </form>
  );
}

function DismissForm({
  workspaceId,
  itemId,
  returnTo,
}: {
  workspaceId: string;
  itemId: string;
  returnTo: string;
}) {
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/disposition`}
      method="POST"
      className="contents"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action" value="dismiss" />
      <input type="hidden" name="return_to" value={returnTo} />
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.08]"
      >
        Dismiss
      </button>
    </form>
  );
}
