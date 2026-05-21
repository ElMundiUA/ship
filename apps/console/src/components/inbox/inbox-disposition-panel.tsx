/**
 * Per-type disposition actions (accept / dismiss / approve / reject /
 * answer / retry / acknowledge), rendered in the inbox preview pane.
 *
 * This is the "legacy" resolution system that complements the newer
 * ``action_items`` flow (see ``inbox-action-panel.tsx``): letters that
 * carry structured ``payload.action_items`` get the action panel; the
 * rest (knowledge drafts, approvals, plain reports) get these
 * type-driven disposition buttons. Both now live in the single preview
 * surface — the standalone ``/inbox/[id]`` detail route was retired.
 *
 * Plain server ``<form action="/api/inbox/[id]/disposition">`` POSTs so
 * the buttons work with JS off and need no client bundle.
 */

import type { InboxType } from "@/lib/inbox-types";

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
  confirm?: string;
};

// Mirrors the planning doc table. The primary is the default verb for
// the type; secondaries cover alternates. ``improvement`` accept ↔
// dismiss drives the knowledge-draft publish ↔ archive side-effects.
const DISPOSITION_BY_TYPE: Record<
  InboxType,
  { primary: ButtonSpec; secondary: ButtonSpec[] }
> = {
  clarification: {
    primary: { action: "answer", label: "Answer", style: "primary" },
    secondary: [
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
  approval: {
    primary: { action: "approve", label: "Approve", style: "primary" },
    secondary: [
      { action: "reject", label: "Reject", style: "danger" },
      { action: "dismiss", label: "Dismiss", style: "secondary" },
    ],
  },
  improvement: {
    primary: { action: "accept", label: "Accept", style: "primary" },
    secondary: [
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
  failure: {
    primary: { action: "retry", label: "Retry", style: "primary" },
    secondary: [
      { action: "resolve", label: "Acknowledge", style: "secondary" },
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
  exception: {
    primary: { action: "acknowledge", label: "Acknowledge", style: "primary" },
    secondary: [
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
  stuck: {
    primary: { action: "resolve", label: "Mark addressed", style: "primary" },
    secondary: [
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
  blocker: {
    primary: { action: "resolve", label: "Mark handled", style: "primary" },
    secondary: [
      { action: "dismiss", label: "Dismiss", style: "danger" },
    ],
  },
  report: {
    primary: { action: "resolve", label: "Acknowledge", style: "primary" },
    secondary: [],
  },
};

function secondaryTone(style: ButtonStyle): string {
  return style === "danger"
    ? "border-coral/40 bg-coral/10 text-coral hover:bg-coral/20"
    : "border-white/15 bg-white/[0.04] text-white/85 hover:bg-white/[0.08]";
}

const PRIMARY_CLASS =
  "inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2 text-sm font-bold text-ink shadow-glow transition hover:brightness-110";

function disposeAction(itemId: string): string {
  return `/api/inbox/${encodeURIComponent(itemId)}/disposition`;
}

export function InboxDispositionPanel({
  workspaceId,
  itemId,
  itemType,
}: {
  workspaceId: string;
  itemId: string;
  itemType: InboxType;
}) {
  const vocab = DISPOSITION_BY_TYPE[itemType] ?? DISPOSITION_BY_TYPE.report;

  if (vocab.primary.action === "answer") {
    return (
      <div className="space-y-3">
        <form action={disposeAction(itemId)} method="POST" className="space-y-2">
          <input type="hidden" name="ws" value={workspaceId} />
          <input type="hidden" name="action" value="answer" />
          <textarea
            name="answer"
            rows={3}
            required
            placeholder="Reply to the agent's question…"
            className="w-full rounded border border-white/10 bg-white/[0.04] px-2 py-1.5 text-sm text-white outline-none focus:border-aqua/40"
          />
          <button type="submit" className={PRIMARY_CLASS}>
            Answer &amp; resolve
          </button>
        </form>
        {vocab.secondary.length > 0 && (
          <div className="flex flex-wrap gap-2 border-t border-white/10 pt-3">
            {vocab.secondary.map((spec) => (
              <DispositionButton
                key={spec.action}
                workspaceId={workspaceId}
                itemId={itemId}
                spec={spec}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <form action={disposeAction(itemId)} method="POST">
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="action" value={vocab.primary.action} />
        <button type="submit" className={PRIMARY_CLASS}>
          {vocab.primary.label}
        </button>
      </form>
      {vocab.secondary.map((spec) => (
        <DispositionButton
          key={spec.action}
          workspaceId={workspaceId}
          itemId={itemId}
          spec={spec}
        />
      ))}
    </div>
  );
}

function DispositionButton({
  workspaceId,
  itemId,
  spec,
}: {
  workspaceId: string;
  itemId: string;
  spec: ButtonSpec;
}) {
  return (
    <form action={disposeAction(itemId)} method="POST">
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="action" value={spec.action} />
      <button
        type="submit"
        className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition ${secondaryTone(spec.style)}`}
      >
        {spec.label}
      </button>
    </form>
  );
}
