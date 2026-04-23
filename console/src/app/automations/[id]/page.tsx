import { LaneDetailView } from "../../lanes/lane-detail-view";

/**
 * ``/automations/[id]`` — new IA mount point for the automation
 * detail surface (RFC-0010 / P1-01). Re-uses the legacy lane detail
 * body; only the breadcrumb / kicker labelling differs. Sibling
 * subagent D will redirect ``/lanes/[id] → /automations/[id]``.
 */

export const dynamic = "force-dynamic";

export default async function AutomationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <LaneDetailView
      laneRowId={id}
      basePath="/automations"
      backLabel="← All automations"
      kicker={(detail) => `${detail.repo_full_name} · automation`}
    />
  );
}
