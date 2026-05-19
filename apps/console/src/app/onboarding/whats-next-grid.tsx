/**
 * ELS-186 (W10) — post-onboarding "what's next" tiles.
 *
 * Three CTAs ordered by likely next action:
 *
 *   1. Plan your first project — primary aqua tile linking to /chat,
 *      where the Navigator's mass-planning intake takes a requirements
 *      doc and drafts the epic graph (ELS-168..176).
 *   2. Triage Inbox — operator's daily-driver surface
 *      (Inbox Decision UI, ELS-158..167).
 *   3. Tune the process — pick agents per stage, mute, rename lanes.
 *
 * Knowledge dropped to a footer link since it's a Day-3 destination,
 * not a Day-1 CTA.
 */

import Link from "next/link";

export function WhatsNextGrid({
  workspaceId,
}: {
  workspaceId: string | null;
}) {
  const wsQuery = workspaceId ? `?ws=${encodeURIComponent(workspaceId)}` : "";

  return (
    <section data-testid="onboarding-done-whats-next" className="mt-8">
      <h2 className="font-display text-lg font-bold text-white">
        What&apos;s next
      </h2>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
        {/* Primary CTA — mass-planning intake. Filled aqua so it
            stands out from the other two ghost tiles. */}
        <Link
          href={`/chat${wsQuery}${wsQuery ? "&" : "?"}intent=plan`}
          data-testid="onboarding-done-whats-next-tile"
          className="group rounded-2xl border border-aqua/40 bg-aqua/[0.10] p-4 transition hover:border-aqua hover:bg-aqua/[0.16]"
        >
          <h3 className="font-display text-base font-bold text-aqua">
            Plan your first project →
          </h3>
          <p className="mt-1 text-[11px] leading-relaxed text-white/75">
            Open chat and tell Ship what you&apos;re building. Drop a
            requirements PDF and Navigator drafts the epics with
            dependencies — preview before commit.
          </p>
        </Link>

        <Link
          href={`/inbox${wsQuery}`}
          data-testid="onboarding-done-whats-next-tile"
          className="group rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4 transition hover:border-aqua/40 hover:bg-aqua/[0.04]"
        >
          <h3 className="font-display text-base font-bold text-white group-hover:text-aqua">
            Triage Inbox →
          </h3>
          <p className="mt-1 text-[11px] leading-relaxed text-white/65">
            Decide in one click — clarifications, approvals, and
            blockers land here as pills and checkboxes.
          </p>
        </Link>

        <Link
          href={`/process${wsQuery}`}
          data-testid="onboarding-done-whats-next-tile"
          className="group rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4 transition hover:border-aqua/40 hover:bg-aqua/[0.04]"
        >
          <h3 className="font-display text-base font-bold text-white group-hover:text-aqua">
            Tune the process →
          </h3>
          <p className="mt-1 text-[11px] leading-relaxed text-white/65">
            Each tracker state runs a specialist. Pick stronger
            agents, mute states, or rename lanes.
          </p>
        </Link>
      </div>

      <p className="mt-4 text-[11px] text-white/45">
        Curating knowledge buckets?{" "}
        <Link
          href={`/knowledge${wsQuery}`}
          className="text-white/65 underline-offset-2 hover:text-white hover:underline"
        >
          Open Knowledge →
        </Link>
      </p>
    </section>
  );
}
