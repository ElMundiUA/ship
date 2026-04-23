import { RunsView } from "../pipelines/runs-view";

/**
 * ``/runs`` — new IA mount point for the run-history surface
 * (RFC-0010 §3 / P1-01). Same body as the legacy ``/pipelines``
 * page, just relabeled. Sibling subagent D wires the legacy
 * redirect; until then both routes resolve to the same view.
 */

export const dynamic = "force-dynamic";

export default async function RunsPage() {
  return <RunsView basePath="/runs" kicker="HISTORY" title="Runs" />;
}
