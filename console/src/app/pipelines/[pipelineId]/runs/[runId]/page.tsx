/**
 * Legacy run-detail route — keeps the long ``/pipelines/<id>/runs/<id>``
 * URL shape working while the new IA migrates to ``/runs/[id]``.
 *
 * Inbox-redesign sprint (P1-01): the heavy implementation lives in
 * :func:`RunDetailView` (sibling module). This page is a thin
 * wrapper that picks ``basePath`` / breadcrumb labels appropriate
 * for the legacy URL.
 */

import { RunDetailView } from "../../../run-detail-view";

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ pipelineId: string; runId: string }>;
  searchParams: Promise<{ ws?: string }>;
};

export default async function PipelineRunDetailPage({ params, searchParams }: PageProps) {
  const { pipelineId, runId } = await params;
  const { ws: wsQuery } = await searchParams;
  return (
    <RunDetailView
      pipelineId={pipelineId}
      runId={runId}
      wsQuery={wsQuery}
      basePath="/pipelines"
      indexPath="/pipelines"
      indexLabel="Pipelines"
      backHref="/"
      backLabel="← Dashboard"
    />
  );
}
