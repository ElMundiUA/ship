/**
 * Reusable escalation deeplink card for the ``/runs/[id]`` detail
 * surface (RFC-0010 Wave 6 / Phase 3 ticket P3-05).
 *
 * Renders one ``run_escalations`` row joined with its target
 * ``inbox_items``. The card is a deeplink into the inbox detail
 * page so operators can pick up the human disposition flow with
 * one click.
 *
 * The inbox-item type drives the icon + the human label
 * ("Approval needed", "Question", …) so the card reads at a glance.
 * Status / owner render as a thin meta-line under the title; both
 * are best-effort (the join may not yet have shipped server-side
 * — see ``listRunEscalations`` TODO).
 */

import Link from "next/link";

import type { ApiRunEscalation } from "@/lib/api/client";
import { Badge } from "@/components/ui";

const TYPE_GLYPH: Record<string, string> = {
  clarification: "\u{2754}", // ❔
  improvement: "\u{2728}", // ✨
  failure: "\u{1F525}", // 🔥
  approval: "\u{1F6A6}", // 🚦
  exception: "\u{26A0}", // ⚠
};

const TYPE_LABEL: Record<string, string> = {
  clarification: "Question",
  improvement: "Improvement",
  failure: "Failure",
  approval: "Approval needed",
  exception: "Exception",
};

const STATUS_TONE: Record<
  string,
  "info" | "warn" | "ok" | "neutral"
> = {
  new: "info",
  snoozed: "warn",
  resolved: "ok",
  dismissed: "neutral",
};

function ownerLabel(
  owner: NonNullable<ApiRunEscalation["inbox_item"]>["owner"],
): string {
  if (!owner) return "unassigned";
  return owner.display_name?.trim() || owner.email;
}

export function EscalationCard({
  escalation,
}: {
  escalation: ApiRunEscalation;
}) {
  const item = escalation.inbox_item;
  // Render even without the join (BE may not have shipped it yet);
  // the deeplink still works because we have the inbox_item_id on
  // the escalation row itself.
  const type = item?.type ?? "clarification";
  const glyph = TYPE_GLYPH[type] ?? "\u{1F4E5}"; // 📥
  const label = TYPE_LABEL[type] ?? type;
  const title = item?.title ?? "Linked inbox item";
  const status = item?.status ?? null;
  const tone: "info" | "warn" | "ok" | "neutral" = status
    ? (STATUS_TONE[status] ?? "neutral")
    : "neutral";

  return (
    <Link
      href={`/inbox/${encodeURIComponent(escalation.inbox_item_id)}`}
      className="group block rounded-xl border border-white/10 bg-white/[0.03] p-3 transition hover:border-aqua/40 hover:bg-white/[0.06]"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.04] text-base"
          >
            {glyph}
          </span>
          <span className="text-[10px] font-bold uppercase tracking-widest text-white/55">
            {label}
          </span>
        </div>
        <span
          aria-hidden
          className="text-xs text-white/55 transition group-hover:text-aqua"
        >
          {"\u2192"}
        </span>
      </div>
      <p className="mt-2 line-clamp-2 text-sm font-semibold text-white/90">
        {title}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-white/55">
        {status && <Badge tone={tone}>{status}</Badge>}
        {item && (
          <span>
            assigned: <span className="text-white/75">{ownerLabel(item.owner)}</span>
          </span>
        )}
        <span className="text-white/40">
          via <code className="rounded bg-white/[0.06] px-1 py-0.5 text-[10px]">{escalation.escalation_reason}</code>
        </span>
      </div>
      <p className="mt-2 text-[11px] font-semibold text-aqua/85 group-hover:text-aqua">
        Open in Inbox {"\u2192"}
      </p>
    </Link>
  );
}
