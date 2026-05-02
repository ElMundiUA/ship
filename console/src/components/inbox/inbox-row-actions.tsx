"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import type { InboxItem, InboxType } from "@/lib/inbox-types";

/**
 * Inline actions on an Inbox list row.
 *
 * Three buttons rendered to the right of the owner pill:
 *   - Primary action — the canonical disposition for this item type
 *     (Approve / Reject for approvals, Accept for improvements, Retry
 *     for failures, Acknowledge for exceptions). One click → POST
 *     /disposition → row disappears on next refresh.
 *   - Answer (clarifications only) — routes into the detail page where
 *     the textarea lives. Inline answer would need a popover; deferred
 *     to keep this pass small.
 *   - Discuss with Navigator — POST /discuss → router.push("/chat?thread=…")
 *     so the operator can draft an answer with the workspace assistant.
 *
 * The component swallows row-level link clicks (`stopPropagation` on
 * the wrapper) so clicking a button never accidentally navigates into
 * the detail page.
 */

type Props = {
  workspaceId: string;
  item: Pick<InboxItem, "id" | "type" | "status">;
  /** Detail page href; used when Answer needs the textarea. */
  detailHref: string;
};

const PRIMARY_LABEL: Record<InboxType, string | null> = {
  clarification: null, // Answer goes to detail (needs typing)
  approval: "Approve",
  improvement: "Accept",
  failure: "Retry",
  exception: "Acknowledge",
  stuck: "Acknowledge",
  blocker: "Acknowledge",
};

const SECONDARY_LABEL: Record<InboxType, string | null> = {
  clarification: null,
  approval: "Reject",
  improvement: null,
  failure: null,
  exception: null,
  stuck: null,
  blocker: null,
};

const DISCUSS_TYPES = new Set<InboxType>([
  "clarification",
  "blocker",
  "failure",
  "exception",
]);

export function InboxRowActions({ workspaceId, item, detailHref }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState<null | "primary" | "secondary" | "discuss">(null);
  const [error, setError] = useState<string | null>(null);

  const primaryLabel = PRIMARY_LABEL[item.type];
  const secondaryLabel = SECONDARY_LABEL[item.type];
  const showDiscuss = DISCUSS_TYPES.has(item.type);
  const isTerminal = item.status === "resolved" || item.status === "dismissed";

  const onPrimary = async () => {
    if (busy || isTerminal) return;
    setBusy("primary");
    setError(null);
    try {
      const action = primaryActionFor(item.type, "primary");
      if (!action) {
        // Clarification → go to detail page for the textarea.
        router.push(detailHref);
        return;
      }
      await postDisposition(item.id, workspaceId, action);
      router.refresh();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setBusy(null);
    }
  };

  const onSecondary = async () => {
    if (busy || isTerminal) return;
    setBusy("secondary");
    setError(null);
    try {
      const action = primaryActionFor(item.type, "secondary");
      if (!action) return;
      await postDisposition(item.id, workspaceId, action);
      router.refresh();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setBusy(null);
    }
  };

  const onDiscuss = async () => {
    if (busy) return;
    setBusy("discuss");
    setError(null);
    try {
      const url = await postDiscuss(item.id, workspaceId);
      router.push(url);
    } catch (e) {
      setError(formatError(e));
      setBusy(null);
    }
  };

  // The row itself is an <a>; stop the event from bubbling so a button
  // click never doubles as a row navigation.
  return (
    <div
      className="flex shrink-0 items-center gap-1.5"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      {error && (
        <span className="text-[10px] font-semibold text-coral" title={error}>
          ✗
        </span>
      )}

      {primaryLabel ? (
        <button
          type="button"
          onClick={onPrimary}
          disabled={!!busy || isTerminal}
          className="rounded-md border border-aqua/40 bg-aqua/10 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide text-aqua transition hover:border-aqua/70 hover:bg-aqua/20 disabled:opacity-40"
        >
          {busy === "primary" ? "…" : primaryLabel}
        </button>
      ) : item.type === "clarification" ? (
        <button
          type="button"
          onClick={onPrimary}
          disabled={!!busy || isTerminal}
          className="rounded-md border border-sun/40 bg-sun/10 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide text-sun transition hover:border-sun/70 hover:bg-sun/20 disabled:opacity-40"
        >
          Answer
        </button>
      ) : null}

      {secondaryLabel && (
        <button
          type="button"
          onClick={onSecondary}
          disabled={!!busy || isTerminal}
          className="rounded-md border border-coral/40 bg-coral/10 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide text-coral transition hover:border-coral/70 hover:bg-coral/20 disabled:opacity-40"
        >
          {busy === "secondary" ? "…" : secondaryLabel}
        </button>
      )}

      {showDiscuss && (
        <button
          type="button"
          onClick={onDiscuss}
          disabled={!!busy || isTerminal}
          className="rounded-md border border-lilac/40 bg-lilac/10 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide text-lilac transition hover:border-lilac/70 hover:bg-lilac/20 disabled:opacity-40"
          title="Open Navigator chat seeded with this item's context"
        >
          {busy === "discuss" ? "…" : "Discuss"}
        </button>
      )}
    </div>
  );
}

function primaryActionFor(
  type: InboxType,
  kind: "primary" | "secondary",
): "approve" | "reject" | "accept" | "retry" | "acknowledge" | null {
  if (kind === "primary") {
    switch (type) {
      case "approval":
        return "approve";
      case "improvement":
        return "accept";
      case "failure":
        return "retry";
      case "exception":
      case "stuck":
      case "blocker":
        return "acknowledge";
      case "clarification":
        return null;
    }
  } else {
    if (type === "approval") return "reject";
    return null;
  }
  return null;
}

function formatError(e: unknown): string {
  if (e instanceof Error) return e.message;
  return "Action failed. Refresh and try again.";
}

/**
 * Posts to the existing form-shaped route handler at
 * `/api/inbox/[id]/disposition`. The handler 303-redirects to
 * `/inbox/[id]` on success, or to `/inbox/[id]?error=<code>` on
 * failure. We follow the redirect, then read the resolved URL —
 * presence of `error` means the action failed.
 */
async function postDisposition(itemId: string, wsId: string, action: string) {
  const body = new FormData();
  body.set("ws", wsId);
  body.set("action", action);
  const res = await fetch(`/api/inbox/${encodeURIComponent(itemId)}/disposition`, {
    method: "POST",
    body,
  });
  if (!res.ok) throw new Error(`Disposition failed (${res.status}).`);
  const code = new URL(res.url).searchParams.get("error");
  if (code) throw new Error(humanizeError(code));
}

async function postDiscuss(itemId: string, wsId: string): Promise<string> {
  const body = new FormData();
  body.set("ws", wsId);
  const res = await fetch(`/api/inbox/${encodeURIComponent(itemId)}/discuss`, {
    method: "POST",
    body,
  });
  if (!res.ok) throw new Error(`Could not open Navigator chat (${res.status}).`);
  const url = new URL(res.url);
  const code = url.searchParams.get("error");
  if (code) throw new Error(humanizeError(code));
  // The handler redirects to /chat?from_inbox=<id>; return the path part.
  return `${url.pathname}${url.search}`;
}

function humanizeError(code: string): string {
  switch (code) {
    case "forbidden":
      return "You don't have permission for that action.";
    case "not_found":
      return "Item no longer exists.";
    case "state_invalid":
      return "Item already resolved — refresh.";
    case "validation_failed":
      return "Action requires more input — open the item.";
    case "api_unavailable":
      return "API is unavailable.";
    default:
      return `Action failed (${code}).`;
  }
}
