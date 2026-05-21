/**
 * Client-safe inbox /decide helper (ELS-145).
 *
 * ``@/lib/api/client`` is server-only; interactive panels call the
 * Next route handler which proxies to the backend with the session cookie.
 */

import type { InboxItemDetail } from "@/lib/inbox-types";

export type InboxDecideBody = {
  selections?: string[];
  freeform?: string | null;
  action_item_id?: string | null;
  choice?: "primary" | "secondary" | null;
};

export async function decideInboxItemClient(
  workspaceId: string,
  itemId: string,
  body: InboxDecideBody,
): Promise<InboxItemDetail> {
  const res = await fetch(
    `/api/inbox/${encodeURIComponent(itemId)}/decide`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ws: workspaceId, ...body }),
    },
  );
  if (!res.ok) {
    let message = `Could not apply decision (${res.status}).`;
    try {
      const err = (await res.json()) as { error?: string; message?: string };
      if (typeof err.message === "string") message = err.message;
      else if (typeof err.error === "string") message = humanizeError(err.error);
    } catch {
      /* ignore */
    }
    throw new Error(message);
  }
  return (await res.json()) as InboxItemDetail;
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
      return "That decision is not valid for this item.";
    default:
      return "Could not apply decision.";
  }
}
