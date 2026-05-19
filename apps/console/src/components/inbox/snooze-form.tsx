"use client";

/**
 * One-click "Snooze N hours" form for the mailbox preview footer.
 *
 * Lives separately from ``mailbox-footer.tsx`` (a server component)
 * because Next.js refuses to serialise an ``onSubmit`` handler from a
 * Server Component to a Client Component — the error surfaces at
 * runtime with the unhelpful "Application error: a server-side
 * exception" page. Splitting the client bit out keeps the parent
 * server-only and lets this component compute the snooze deadline
 * against the operator's clock instead of stale server-render time.
 *
 * The route at ``/api/inbox/[id]/snooze`` expects a
 * ``datetime-local``-shaped string; we format the local-time deadline
 * (now + ``hours``) before submitting.
 */

export function SnoozeForm({
  workspaceId,
  itemId,
  hours,
  label,
}: {
  workspaceId: string;
  itemId: string;
  hours: number;
  label: string;
}) {
  return (
    <form
      action={`/api/inbox/${encodeURIComponent(itemId)}/snooze`}
      method="POST"
      className="contents"
      onSubmit={(e) => {
        const input = e.currentTarget.elements.namedItem(
          "snoozed_until",
        ) as HTMLInputElement | null;
        if (input) {
          const t = new Date(Date.now() + hours * 60 * 60 * 1000);
          const pad = (n: number) => String(n).padStart(2, "0");
          input.value = `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}T${pad(t.getHours())}:${pad(t.getMinutes())}`;
        }
      }}
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="snoozed_until" defaultValue="" />
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.08]"
      >
        {label}
      </button>
    </form>
  );
}
