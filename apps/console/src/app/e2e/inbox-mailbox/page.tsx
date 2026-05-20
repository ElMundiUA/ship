import { notFound } from "next/navigation";

import { InboxMailboxListClient } from "@/components/inbox/inbox-mailbox-list-client";
import { INBOX_VISUAL_MIXED_ITEMS } from "@/lib/inbox-visual-fixture";

export const dynamic = "force-dynamic";

type Variant = "empty" | "mixed";

/**
 * Deterministic mailbox list for Playwright snapshots — not linked in prod nav.
 * Enable with `SHIP_E2E_INBOX_VISUAL=1` on the console process.
 */
export default async function E2eInboxMailboxVisualPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  if (process.env.SHIP_E2E_INBOX_VISUAL !== "1") {
    notFound();
  }

  const params: Record<string, string | string[] | undefined> =
    (await (searchParams ??
      Promise.resolve({} as Record<string, string | string[] | undefined>))) ??
    {};
  const variantRaw = typeof params.variant === "string" ? params.variant : "mixed";
  const variant: Variant = variantRaw === "empty" ? "empty" : "mixed";
  const items = variant === "empty" ? [] : INBOX_VISUAL_MIXED_ITEMS;
  const selectedId = items[0]?.id ?? null;

  return (
    <div className="min-h-screen bg-[#0a0a0b] p-6">
      <div className="mx-auto max-w-md">
        <InboxMailboxListClient
          items={items}
          ownership="mine"
          selectedId={selectedId}
          ownershipTabs={
            <div className="border-b border-white/[0.06] px-4 py-2 text-xs text-white/40">
              E2E visual fixture ({variant})
            </div>
          }
        />
      </div>
    </div>
  );
}
