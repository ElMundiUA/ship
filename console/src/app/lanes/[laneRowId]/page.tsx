import { LaneDetailView } from "../lane-detail-view";

/**
 * Lane detail page — shows the raw YAML block (pretty-printed),
 * lane metadata, and the 20 most recent ``PipelineRun`` rows keyed
 * to this lane.
 *
 * Inbox-redesign sprint (P1-01): the body lives in
 * :func:`LaneDetailView` (sibling module) so the new
 * ``/automations/[id]`` route can render the same screen with
 * relabeled chrome.
 */

export const dynamic = "force-dynamic";

export default async function LaneDetailPage({
  params,
}: {
  params: Promise<{ laneRowId: string }>;
}) {
  const { laneRowId } = await params;
  return (
    <LaneDetailView
      laneRowId={laneRowId}
      basePath="/lanes"
      backLabel="← All lanes"
      kicker={(detail) => `${detail.repo_full_name} · lane`}
    />
  );
}
