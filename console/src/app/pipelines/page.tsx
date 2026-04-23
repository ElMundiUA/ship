import { RunsView } from "./runs-view";

/**
 * ``/pipelines`` — dedicated lane / pipeline surface (separate from
 * the dashboard's "Recommended actions" strip). Lanes render as
 * swimlanes grouped by repo.
 *
 * Inbox-redesign sprint (RFC-0010 / P1-01): the actual page body
 * lives in :func:`RunsView` (sibling module) so the new ``/runs``
 * route can mount the same screen with relabeled chrome. Sibling
 * subagent D will land the redirect from ``/pipelines → /runs``;
 * until then both routes resolve.
 */

export const dynamic = "force-dynamic";

export default async function PipelinesPage() {
  return <RunsView basePath="/pipelines" title="Pipelines" />;
}
