import type { APIRequestContext } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiGet,
  shipResolveWorkspaceId,
} from "./ship-api";

/** Backend primary process id (see processes.py PRIMARY_PROCESS_ID). */
export const DEVELOPMENT_PROCESS_ID = "development";

export type ActivatedRepo = { id: string; full_name: string };

export type ProcessEditorProbe = {
  workspaceId: string;
  primaryProcessId: string;
  repoId: string | null;
  repos: ActivatedRepo[];
  firstStateId: string | null;
  ready: boolean;
  skipReason?: string;
};

export function processEditorUrl(opts: {
  processId?: string;
  repoId?: string;
  stateId?: string;
  tab?: "schedule" | "routines";
  workspaceId?: string;
}): string {
  const processId = opts.processId ?? DEVELOPMENT_PROCESS_ID;
  const params = new URLSearchParams();
  if (opts.workspaceId) params.set("ws", opts.workspaceId);
  if (opts.repoId) params.set("repo", opts.repoId);
  if (opts.stateId) params.set("state", opts.stateId);
  if (opts.tab) params.set("tab", opts.tab);
  const qs = params.toString();
  return qs
    ? `/process/${encodeURIComponent(processId)}?${qs}`
    : `/process/${encodeURIComponent(processId)}`;
}

/**
 * Probe workspace processes + activated repos before driving the editor UI.
 * Returns null when Ship API credentials are unset (caller should skip).
 */
export async function fetchProcessEditorProbe(
  request: APIRequestContext,
): Promise<ProcessEditorProbe | null> {
  if (!hasShipApiCredentials()) return null;

  const workspaceId = await shipResolveWorkspaceId(request);
  const ws = encodeURIComponent(workspaceId);

  const reposRes = await shipApiGet(request, `/v1/workspaces/${ws}/repos`);
  if (!reposRes.ok()) return null;
  const repos = (await reposRes.json()) as ActivatedRepo[];
  if (repos.length === 0) {
    return {
      workspaceId,
      primaryProcessId: DEVELOPMENT_PROCESS_ID,
      repoId: null,
      repos: [],
      firstStateId: null,
      ready: false,
      skipReason:
        "No activated repos in this workspace — run onboarding or full-journey first.",
    };
  }

  const sandboxRepo = process.env.E2E_SANDBOX_REPO?.trim();
  let repoId = repos[0]!.id;
  if (sandboxRepo) {
    const match = repos.find((r) => r.full_name === sandboxRepo);
    if (match) repoId = match.id;
  }

  const processesRes = await shipApiGet(
    request,
    `/v1/workspaces/${ws}/processes`,
  );
  if (!processesRes.ok()) return null;
  const processList = (await processesRes.json()) as {
    primary_process_id?: string;
    processes?: unknown[];
  };
  if (!processList.processes?.length) {
    return {
      workspaceId,
      primaryProcessId:
        processList.primary_process_id ?? DEVELOPMENT_PROCESS_ID,
      repoId,
      repos,
      firstStateId: null,
      ready: false,
      skipReason: "No processes in workspace.",
    };
  }

  const procRes = await shipApiGet(
    request,
    `/v1/workspaces/${ws}/processes/${encodeURIComponent(DEVELOPMENT_PROCESS_ID)}?repo_id=${encodeURIComponent(repoId)}`,
  );
  if (!procRes.ok()) {
    return {
      workspaceId,
      primaryProcessId:
        processList.primary_process_id ?? DEVELOPMENT_PROCESS_ID,
      repoId,
      repos,
      firstStateId: null,
      ready: false,
      skipReason: `GET /processes/development → ${procRes.status()}`,
    };
  }
  const proc = (await procRes.json()) as { states?: { id: string }[] };
  const firstStateId = proc.states?.[0]?.id ?? null;

  return {
    workspaceId,
    primaryProcessId:
      processList.primary_process_id ?? DEVELOPMENT_PROCESS_ID,
    repoId,
    repos,
    firstStateId,
    ready: Boolean(firstStateId),
    skipReason: firstStateId
      ? undefined
      : "Development process has no stages to open in the inspector.",
  };
}
