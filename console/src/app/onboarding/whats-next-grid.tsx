/**
 * Server component — the four "what's next" CTA tiles rendered at
 * the bottom of the post-onboarding done page (P5-09).
 *
 * Tiles link into the new IA the wave-8c redesign establishes:
 *
 *   - /inbox        — clarification / approval requests as they arrive
 *   - /process      — specialist workflow for the workspace
 *   - /knowledge    — generated project knowledge after bootstrap
 *
 * Server-side because we don't need any interactivity; passing the
 * workspace id through the URL keeps the destination pages happy
 * without an extra client-side workspace lookup.
 */

import Link from "next/link";

export function WhatsNextGrid({
  workspaceId,
  installedCount,
}: {
  workspaceId: string | null;
  /**
   * Number of Plays we installed by default for this wizard run.
   * Surfaced verbatim on the "Browse Plays" tile so the operator
   * knows what's already wired before they go shopping.
   */
  installedCount: number | null;
}) {
  const wsQuery = workspaceId ? `?ws=${encodeURIComponent(workspaceId)}` : "";
  const tiles: {
    href: string;
    title: string;
    blurb: string;
  }[] = [
    {
      href: `/inbox${wsQuery}`,
      title: "Open Inbox →",
      blurb:
        "Clarification and approval requests show up here as agents need a human in the loop.",
    },
    {
      href: `/process${wsQuery}`,
      title: "Open Process →",
      blurb:
        installedCount != null
          ? `Review the seeded development process. The old ${installedCount}-play bundle is no longer the operator model.`
          : "Review the seeded development process and specialist handoffs.",
    },
    {
      href: `/knowledge${wsQuery}`,
      title: "Open Knowledge →",
      blurb:
        "Workspace knowledge buckets live here and are stored in Ship's database.",
    },
  ];

  return (
    <section data-testid="onboarding-done-whats-next" className="mt-8">
      <h2 className="font-display text-lg font-bold text-white">
        What&apos;s next
      </h2>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((tile) => (
          <Link
            key={tile.title}
            href={tile.href}
            data-testid="onboarding-done-whats-next-tile"
            className="group rounded-2xl border border-white/10 bg-white/[0.03] p-4 backdrop-blur-xl transition hover:border-aqua/40 hover:bg-aqua/[0.04]"
          >
            <h3 className="font-display text-base font-bold text-white group-hover:text-aqua">
              {tile.title}
            </h3>
            <p className="mt-1 text-[11px] leading-relaxed text-white/65">
              {tile.blurb}
            </p>
          </Link>
        ))}
      </div>
    </section>
  );
}
