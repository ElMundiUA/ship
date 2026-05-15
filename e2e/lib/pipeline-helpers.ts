/**
 * Ship API wrappers for the Process e2e pipeline suite.
 *
 * Sits next to ``agent-pipeline.ts`` (which owns the shipctl
 * subprocess lifecycle). This module is the read/write surface
 * tests use to plant the starting state and assert the ending
 * state of an agent run.
 *
 * Auth: every call uses the **PO** PAT seeded by
 * ``seed_e2e_pipeline_workspace.py``. The PO has ``workspace.admin``
 * which is what the production tracker/ticket endpoints require.
 */

import type { APIRequestContext } from "@playwright/test";


export type PipelineSuiteEnv = {
  base: string | null;
  workspaceId: string | null;
  poPat: string | null;
  devPat: string | null;
  repoFullName: string | null;
};


export function pipelineSuiteEnv(): PipelineSuiteEnv {
  const base = process.env.E2E_SHIP_API_BASE?.trim().replace(/\/+$/, "");
  const workspaceId = process.env.E2E_PIPELINE_WORKSPACE_ID?.trim();
  const poPat = process.env.E2E_PIPELINE_PO_TOKEN?.trim();
  const devPat = process.env.E2E_PIPELINE_DEV_TOKEN?.trim();
  const repoFullName = process.env.E2E_PIPELINE_REPO?.trim();
  return {
    base: base || null,
    workspaceId: workspaceId || null,
    poPat: poPat || null,
    devPat: devPat || null,
    repoFullName: repoFullName || null,
  };
}


export function hasPipelineCredentials(): boolean {
  const env = pipelineSuiteEnv();
  return Boolean(env.base && env.workspaceId && env.poPat);
}


function authHeaders(token: string): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/json",
    "Content-Type": "application/json",
  };
}


export type ProjectCreated = {
  id: string;
  name: string;
  display_id?: string;
  created: boolean;
};


/**
 * Create (or find) a project in the workspace's bound tracker.
 * Idempotent on case-insensitive name match — re-runs return the
 * existing project with ``created: false``.
 */
export async function pipelineCreateProject(
  request: APIRequestContext,
  env: { base: string; workspaceId: string; poPat: string },
  opts: { name: string; body: string; description?: string },
): Promise<ProjectCreated> {
  const res = await request.post(
    `${env.base}/v1/workspaces/${env.workspaceId}/projects/find-or-create`,
    {
      headers: authHeaders(env.poPat),
      data: JSON.stringify({
        name: opts.name,
        body: opts.body,
        description: opts.description,
      }),
    },
  );
  if (!res.ok()) {
    throw new Error(
      `POST /projects/find-or-create → ${res.status()}: ${await res.text()}`,
    );
  }
  const json = (await res.json()) as {
    created: boolean;
    project: { id: string; name: string; display_id?: string };
  };
  return {
    id: json.project.id,
    name: json.project.name,
    display_id: json.project.display_id,
    created: json.created,
  };
}


export type TicketCreated = {
  ticketRef: string;
  url: string | null;
};


/**
 * Create a ticket in the named project. ``project_id`` is the
 * tracker-native id returned by ``pipelineCreateProject``.
 */
export async function pipelineCreateTicket(
  request: APIRequestContext,
  env: { base: string; workspaceId: string; poPat: string },
  opts: {
    projectId: string;
    title: string;
    body: string;
    labels?: string[];
    priority?: number;
  },
): Promise<TicketCreated> {
  const res = await request.post(
    `${env.base}/v1/workspaces/${env.workspaceId}/tracker/tickets`,
    {
      headers: authHeaders(env.poPat),
      data: JSON.stringify({
        project_id: opts.projectId,
        title: opts.title,
        body: opts.body,
        labels: opts.labels ?? [],
        priority: opts.priority,
      }),
    },
  );
  if (!res.ok()) {
    throw new Error(
      `POST /tracker/tickets → ${res.status()}: ${await res.text()}`,
    );
  }
  const json = (await res.json()) as { ticket_ref: string; url?: string };
  return { ticketRef: json.ticket_ref, url: json.url ?? null };
}


export type TicketDetail = {
  display_id: string;
  title: string;
  body: string;
  state: string;
  labels: string[];
  comments: { id: string; body: string; author: string; created_at: string }[];
};


/**
 * Memory-tracker ticket read. The Linear gateway has its own
 * endpoint; for the pipeline suite we live entirely on the memory
 * adapter so ``/local-tracker/tickets/{display_id}`` is the right
 * source of truth.
 */
export async function pipelineGetTicket(
  request: APIRequestContext,
  env: { base: string; workspaceId: string; poPat: string },
  displayId: string,
): Promise<TicketDetail> {
  const res = await request.get(
    `${env.base}/v1/workspaces/${env.workspaceId}/local-tracker/tickets/${displayId}`,
    { headers: authHeaders(env.poPat) },
  );
  if (!res.ok()) {
    throw new Error(
      `GET /local-tracker/tickets/${displayId} → ${res.status()}: ${await res.text()}`,
    );
  }
  return (await res.json()) as TicketDetail;
}


/**
 * Extract the ``stage:<name>`` label from a ticket. Returns null
 * when no stage label is present (in-flight or pre-classification).
 */
export function extractStage(ticket: TicketDetail): string | null {
  for (const label of ticket.labels) {
    if (label.startsWith("stage:")) return label.slice(6);
  }
  return null;
}


/**
 * Bump a project's priority bucket to ``active`` so the dispatcher's
 * ELS-80 gate lets agents claim tickets from it. ``find-or-create``
 * lands new projects in the ``planning`` bucket (drafts) — the PO
 * has to promote them explicitly through the Console dashboard
 * before agents pick up work. The e2e suite bypasses that human
 * gate by calling this immediately after creating the project.
 *
 * Pass ``state="parked"`` to assert the decomposition completion
 * hook's behaviour without flipping back to drafts.
 */
export async function pipelineSetProjectState(
  request: APIRequestContext,
  env: { base: string; workspaceId: string; poPat: string },
  projectId: string,
  state: "active" | "planning" | "parked" = "active",
): Promise<void> {
  const res = await request.post(
    `${env.base}/v1/workspaces/${env.workspaceId}/priorities/state`,
    {
      headers: authHeaders(env.poPat),
      data: JSON.stringify({
        project_native_id: projectId,
        state,
      }),
    },
  );
  if (!res.ok()) {
    throw new Error(
      `POST /priorities/state → ${res.status()}: ${await res.text()}`,
    );
  }
}

/** Back-compat alias — kept while existing A1 spec uses the old name. */
export const pipelineActivateProject = (
  request: APIRequestContext,
  env: { base: string; workspaceId: string; poPat: string },
  projectId: string,
): Promise<void> => pipelineSetProjectState(request, env, projectId, "active");


export type ProjectSnapshot = {
  id: string;
  slug: string;
  name: string;
  state: string;
  body: string | null;
  description: string | null;
  ticket_count: number;
};


export type PriorityBucket = "planning" | "active" | "parked";


/**
 * Read the dashboard priorities bucket for a project. Distinct from
 * ``ProjectSnapshot.state``: that's the tracker-native project
 * state (Linear's project workflow), while this is the Ship-side
 * priority row that the dispatcher's ELS-80 gate keys on.
 *
 * Returns ``null`` when no priorities row exists yet (project
 * created outside Ship's onboarding flow).
 */
export async function pipelineGetProjectPriority(
  request: APIRequestContext,
  env: { base: string; workspaceId: string; poPat: string },
  projectId: string,
): Promise<PriorityBucket | null> {
  const res = await request.get(
    `${env.base}/v1/workspaces/${env.workspaceId}/priorities`,
    { headers: authHeaders(env.poPat) },
  );
  if (!res.ok()) {
    throw new Error(
      `GET /priorities → ${res.status()}: ${await res.text()}`,
    );
  }
  const json = (await res.json()) as {
    projects: { project_native_id: string; priority_state: PriorityBucket }[];
  };
  const row = json.projects.find((p) => p.project_native_id === projectId);
  return row?.priority_state ?? null;
}


/**
 * Fetch the project snapshot from the local-tracker dashboard. The
 * dashboard route is the only public surface that exposes the
 * project ``body`` (where decomposition's WBS / Architecture /
 * Test architecture sections live).
 */
export async function pipelineGetProject(
  request: APIRequestContext,
  env: { base: string; workspaceId: string; poPat: string },
  projectId: string,
): Promise<ProjectSnapshot> {
  const res = await request.get(
    `${env.base}/v1/workspaces/${env.workspaceId}/local-tracker/dashboard`,
    { headers: authHeaders(env.poPat) },
  );
  if (!res.ok()) {
    throw new Error(
      `GET /local-tracker/dashboard → ${res.status()}: ${await res.text()}`,
    );
  }
  const json = (await res.json()) as { projects: ProjectSnapshot[] };
  const project = json.projects.find((p) => p.id === projectId);
  if (!project) {
    throw new Error(
      `project ${projectId} missing from /local-tracker/dashboard response`,
    );
  }
  return project;
}


/**
 * Move a ticket to a new FSM stage. Wraps the local-tracker
 * ``/tickets/{display_id}/transition`` endpoint. ``to_state`` may
 * be either a display state ("Todo" / "In Progress" / "Done") or
 * an FSM-stage name ("planning" / "dev_implementation" / …); the
 * adapter swaps the ``stage:*`` label accordingly.
 */
export async function pipelineTransitionTicket(
  request: APIRequestContext,
  env: { base: string; workspaceId: string; poPat: string },
  displayId: string,
  toState: string,
): Promise<void> {
  const res = await request.post(
    `${env.base}/v1/workspaces/${env.workspaceId}/local-tracker/tickets/${displayId}/transition`,
    {
      headers: authHeaders(env.poPat),
      data: JSON.stringify({ to_state: toState }),
    },
  );
  if (!res.ok()) {
    throw new Error(
      `POST /local-tracker/tickets/${displayId}/transition → ${res.status()}: ${await res.text()}`,
    );
  }
}


export type ProjectTicketRow = {
  ticket_ref: string;
  title: string;
  state: string | null;
  url: string | null;
  labels: string[];
};


/**
 * List child tickets under a project. The route lives on the
 * tracker-routed gateway, so it works against any tracker (Linear /
 * memory) without a separate code path.
 *
 * Returns ``ProjectTicketRow`` — a thinner shape than
 * ``TicketDetail`` (no body, no comments). Callers that need the
 * full detail should follow up with ``pipelineGetTicket`` on the
 * ``ticket_ref``.
 */
export async function pipelineListProjectTickets(
  request: APIRequestContext,
  env: { base: string; workspaceId: string; poPat: string },
  projectId: string,
): Promise<ProjectTicketRow[]> {
  const res = await request.get(
    `${env.base}/v1/workspaces/${env.workspaceId}/tracker/projects/${projectId}/tickets`,
    { headers: authHeaders(env.poPat) },
  );
  if (!res.ok()) {
    throw new Error(
      `GET /tracker/projects/${projectId}/tickets → ${res.status()}: ${await res.text()}`,
    );
  }
  const json = (await res.json()) as { tickets: ProjectTicketRow[] };
  return json.tickets;
}
