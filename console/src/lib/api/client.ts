/**
 * Server-side fetch helpers for the Ship `/v1` API.
 *
 * The console always calls the backend from server components / server actions
 * — never from the browser directly. That keeps the session token in an
 * httpOnly cookie and lets us short-circuit gracefully back to mock data when
 * the backend is unavailable (for the marketing-style preview deployment).
 */

import "server-only";

import type {
  ApiArtifact,
  ApiArtifactDetail,
  ApiArtifactKind,
  ApiArtifactRepo,
  ApiAuditPage,
  ApiIntegration,
  ApiIntegrationKind,
  ApiKnowledgeBucket,
  ApiMember,
  ApiMemberRole,
  ApiSession,
  ApiTokenInfo,
  ApiTokenMint,
  ApiUser,
  ApiWorkspace,
} from "./types";
import { getSessionToken } from "./session";

const PLURAL: Record<ApiArtifactKind, string> = {
  pattern: "patterns",
  tool: "tools",
  workflow: "workflows",
  collection: "collections",
};

export class ApiUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiUnavailableError";
  }
}

export class ApiHttpError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.name = "ApiHttpError";
    this.status = status;
    this.detail = detail;
  }
}

function baseUrl(): string | null {
  // SHIP_API_URL is set in docker-compose (`http://ship-server:8100`) and can
  // be set in `.env.local` for local dev. When unset, every consumer falls
  // back to the mock fixtures.
  const url = process.env.SHIP_API_URL?.trim();
  return url && url.length > 0 ? url.replace(/\/+$/, "") : null;
}

export function isApiConfigured(): boolean {
  return baseUrl() !== null;
}

type FetchOpts = {
  method?: string;
  body?: unknown;
  /** Override the bearer token (e.g. right after login, before the cookie is set). */
  token?: string | null;
  /** Pass-through `next.revalidate` for caching control. */
  revalidate?: number | false;
  signal?: AbortSignal;
};

export async function apiFetch<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const base = baseUrl();
  if (base === null) {
    throw new ApiUnavailableError("SHIP_API_URL is not set");
  }
  const token = opts.token === undefined ? await getSessionToken() : opts.token;
  const headers: Record<string, string> = { accept: "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["content-type"] = "application/json";

  const init: RequestInit & { next?: { revalidate?: number | false } } = {
    method: opts.method ?? "GET",
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    signal: opts.signal,
  };
  if (opts.revalidate !== undefined) {
    init.next = { revalidate: opts.revalidate };
  } else if ((opts.method ?? "GET") === "GET") {
    // Per-request data; never cache between users.
    init.cache = "no-store";
  }

  let res: Response;
  try {
    res = await fetch(`${base}${path}`, init);
  } catch (err) {
    throw new ApiUnavailableError(
      `cannot reach ${base}${path}: ${err instanceof Error ? err.message : String(err)}`,
    );
  }

  const text = await res.text();
  let data: unknown = null;
  if (text.length > 0) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? (data as { detail: unknown }).detail
        : data;
    const summary =
      typeof detail === "string" ? detail : `HTTP ${res.status} on ${path}`;
    throw new ApiHttpError(res.status, detail, summary);
  }
  return data as T;
}

// --- Auth -------------------------------------------------------------------

export function login(email: string, password: string): Promise<ApiSession> {
  return apiFetch<ApiSession>("/v1/auth/local/login", {
    method: "POST",
    body: { email, password },
    token: null,
  });
}

export function signup(
  email: string,
  password: string,
  display_name?: string,
): Promise<ApiSession> {
  return apiFetch<ApiSession>("/v1/auth/local/signup", {
    method: "POST",
    body: { email, password, display_name },
    token: null,
  });
}

export function getMe(token?: string): Promise<ApiUser> {
  return apiFetch<ApiUser>("/v1/auth/me", { token });
}

// --- Workspaces -------------------------------------------------------------

export function listWorkspaces(token?: string): Promise<ApiWorkspace[]> {
  return apiFetch<ApiWorkspace[]>("/v1/workspaces", { token });
}

export function createWorkspace(input: {
  name: string;
  slug: string;
}): Promise<ApiWorkspace> {
  return apiFetch<ApiWorkspace>("/v1/workspaces", {
    method: "POST",
    body: input,
  });
}

export function updateWorkspace(
  workspaceId: string,
  input: {
    name?: string;
    catalog_sources?: Record<string, boolean>;
  },
  token?: string,
): Promise<ApiWorkspace> {
  return apiFetch<ApiWorkspace>(`/v1/workspaces/${workspaceId}`, {
    method: "PATCH",
    body: input,
    token,
  });
}

// --- Workspace artifacts ----------------------------------------------------

export async function listArtifacts(
  workspaceId: string,
  kind: ApiArtifactKind,
): Promise<ApiArtifact[]> {
  const plural = PLURAL[kind];
  const payload = await apiFetch<Record<string, unknown>>(
    `/v1/workspaces/${workspaceId}/artifacts/${kind}`,
  );
  const list = payload[plural];
  return Array.isArray(list) ? (list as ApiArtifact[]) : [];
}

export async function listAllArtifacts(workspaceId: string): Promise<ApiArtifact[]> {
  const kinds: ApiArtifactKind[] = ["pattern", "tool", "workflow", "collection"];
  const results = await Promise.all(kinds.map((k) => listArtifacts(workspaceId, k).then(
    (rows) => rows.map((r) => ({ ...r, _kind: k })),
  )));
  return results.flat() as ApiArtifact[];
}

/** Look up the detail for a given artifact id, probing every kind in order. */
export async function getArtifactById(
  workspaceId: string,
  artifactId: string,
): Promise<ApiArtifactDetail | null> {
  const kinds: ApiArtifactKind[] = ["workflow", "tool", "pattern", "collection"];
  for (const kind of kinds) {
    try {
      return await apiFetch<ApiArtifactDetail>(
        `/v1/workspaces/${workspaceId}/artifacts/${kind}/${encodeURIComponent(artifactId)}`,
      );
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 404) continue;
      throw err;
    }
  }
  return null;
}

// --- Integrations ----------------------------------------------------------

export function listIntegrations(
  workspaceId: string,
  token?: string,
): Promise<ApiIntegration[]> {
  return apiFetch<ApiIntegration[]>(
    `/v1/workspaces/${workspaceId}/integrations`,
    { token },
  );
}

export function upsertIntegration(
  workspaceId: string,
  kind: string,
  input: { config: Record<string, unknown>; secret?: string | null },
  token?: string,
): Promise<ApiIntegration> {
  return apiFetch<ApiIntegration>(
    `/v1/workspaces/${workspaceId}/integrations/${kind}`,
    {
      method: "PUT",
      body: { kind, config: input.config, secret: input.secret ?? null },
      token,
    },
  );
}

export function deleteIntegration(
  workspaceId: string,
  kind: string,
  token?: string,
): Promise<void> {
  return apiFetch<void>(
    `/v1/workspaces/${workspaceId}/integrations/${kind}`,
    { method: "DELETE", token },
  );
}

export function probeIntegration(
  workspaceId: string,
  kind: string,
  token?: string,
): Promise<ApiIntegration> {
  return apiFetch<ApiIntegration>(
    `/v1/workspaces/${workspaceId}/integrations/${kind}/probe`,
    { method: "POST", token },
  );
}

// --- GitHub App install (Day 1 WOW-onboarding flow) -----------------------

/**
 * Response from `POST /v1/integrations/github/install/start`.
 *
 * The console redirects the browser to `install_url`; GitHub bounces back
 * to the backend's `/install/callback` once the user picks repos, and the
 * backend then 302s the browser back into `/onboarding?step=tracker` on
 * the configured console origin.
 */
export interface ApiGitHubInstallStart {
  install_url: string;
  state: string;
}

export function startGitHubAppInstall(
  workspaceId: string,
  token?: string,
): Promise<ApiGitHubInstallStart> {
  // Workspace id travels as a query param so the backend can scope auth +
  // membership without us having to choose between path-style ("nice URL")
  // and body-style ("survives form-redirects"). Query is the natural fit
  // for an idempotent admin-only initiator.
  const path = `/v1/integrations/github/install/start?workspace_id=${encodeURIComponent(workspaceId)}`;
  return apiFetch<ApiGitHubInstallStart>(path, { method: "POST", token });
}

// --- Workspace repos (Day-2 picker) ----------------------------------------

/**
 * One row of the picker UI. Mirrors the backend `AvailableRepoOut`
 * shape. ``activated`` reflects our DB state for the workspace; the rest
 * is whatever the GitHub installation API returned at request time.
 */
export interface ApiAvailableRepo {
  external_id: number;
  full_name: string;
  owner: string;
  name: string;
  default_branch: string;
  private: boolean;
  html_url: string;
  description: string | null;
  activated: boolean;
}

export interface ApiActivatedRepo {
  id: string;
  external_id: number;
  full_name: string;
  default_branch: string;
  private: boolean;
  html_url: string;
  description: string | null;
  activated_at: string | null;
  provider: string;
  /**
   * Catalog preset id (``web-app`` / ``api-backend`` / …) attached to
   * this repo during activation. ``null`` for legacy rows activated
   * before Phase 2 — the backend treats ``null`` as
   * ``adoption-minimum``-shaped defaults.
   */
  preset: string | null;
}

export function listAvailableRepos(
  workspaceId: string,
  token?: string,
): Promise<ApiAvailableRepo[]> {
  return apiFetch<ApiAvailableRepo[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/available`,
    { token },
  );
}

export function listActivatedRepos(
  workspaceId: string,
  token?: string,
): Promise<ApiActivatedRepo[]> {
  return apiFetch<ApiActivatedRepo[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos`,
    { token },
  );
}

export function activateRepos(
  workspaceId: string,
  externalIds: number[],
  options: { preset?: string | null; token?: string } = {},
): Promise<ApiActivatedRepo[]> {
  const body: Record<string, unknown> = { external_ids: externalIds };
  if (options.preset) body.preset = options.preset;
  return apiFetch<ApiActivatedRepo[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/activate`,
    {
      method: "POST",
      body,
      token: options.token,
    },
  );
}

// --- Team invites (B7) ------------------------------------------------------

export interface ApiInvite {
  id: string;
  email: string;
  role: string;
  invited_by_email: string | null;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
  /** Only populated on the ``POST`` create response, exactly once. */
  token: string | null;
  /** Convenience accept URL for the admin to copy/forward. */
  accept_url: string | null;
}

export interface ApiInvitePeek {
  email: string;
  role: string;
  workspace_id: string;
  workspace_name: string;
  invited_by_email: string | null;
  expires_at: string;
}

export interface ApiInviteAccept {
  workspace_id: string;
  role: string;
}

export function listInvites(
  workspaceId: string,
  token?: string,
): Promise<ApiInvite[]> {
  return apiFetch<ApiInvite[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/invites`,
    { token },
  );
}

export function createInvites(
  workspaceId: string,
  payload: {
    emails?: string;
    invites?: { email: string; role: string }[];
    default_role?: string;
    ttl_days?: number;
  },
  options: { token?: string } = {},
): Promise<ApiInvite[]> {
  return apiFetch<ApiInvite[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/invites`,
    { method: "POST", body: payload, token: options.token },
  );
}

export function revokeInvite(
  workspaceId: string,
  inviteId: string,
  options: { token?: string } = {},
): Promise<void> {
  return apiFetch<void>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/invites/${encodeURIComponent(inviteId)}`,
    { method: "DELETE", token: options.token },
  );
}

/** Unauthenticated peek — the invite token itself is the capability. */
export function peekInvite(inviteToken: string): Promise<ApiInvitePeek> {
  return apiFetch<ApiInvitePeek>(
    `/v1/invites/${encodeURIComponent(inviteToken)}`,
    { token: null },
  );
}

export function acceptInvite(
  inviteToken: string,
  options: { token?: string } = {},
): Promise<ApiInviteAccept> {
  return apiFetch<ApiInviteAccept>(
    `/v1/invites/${encodeURIComponent(inviteToken)}/accept`,
    { method: "POST", token: options.token },
  );
}

// --- Clarifications inbox (C9) ---------------------------------------------

export type ApiClarificationStatus =
  | "open"
  | "answered"
  | "skipped"
  | "stale";

export interface ApiClarification {
  id: string;
  workspace_id: string;
  repo_id: string | null;
  pipeline_run_id: string | null;
  ticket_ref: string | null;
  question: string;
  answer: string | null;
  status: ApiClarificationStatus;
  context: Record<string, unknown>;
  answered_by_email: string | null;
  answered_at: string | null;
  created_at: string;
  updated_at: string;
}

export function listClarifications(
  workspaceId: string,
  opts: { status?: ApiClarificationStatus; token?: string } = {},
): Promise<ApiClarification[]> {
  const qs = opts.status ? `?status=${encodeURIComponent(opts.status)}` : "";
  return apiFetch<ApiClarification[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/clarifications${qs}`,
    { token: opts.token },
  );
}

export function createClarification(
  workspaceId: string,
  payload: {
    question: string;
    ticket_ref?: string | null;
    repo_id?: string | null;
    context?: Record<string, unknown>;
  },
  options: { token?: string } = {},
): Promise<ApiClarification> {
  return apiFetch<ApiClarification>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/clarifications`,
    { method: "POST", body: payload, token: options.token },
  );
}

export function updateClarification(
  workspaceId: string,
  clarificationId: string,
  patch: {
    answer?: string | null;
    status?: "open" | "answered" | "skipped";
  },
  options: { token?: string } = {},
): Promise<ApiClarification> {
  return apiFetch<ApiClarification>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/clarifications/${encodeURIComponent(clarificationId)}`,
    { method: "PATCH", body: patch, token: options.token },
  );
}

// --- Improvements (C8) -----------------------------------------------------

export type ApiImprovementDecision =
  | "pending"
  | "accepted"
  | "declined"
  | "deferred";

export interface ApiImprovement {
  id: string;
  workspace_id: string;
  repo_id: string | null;
  pipeline_run_id: string | null;
  kind: string;
  title: string;
  body: string;
  impact: string | null;
  effort: string | null;
  context: Record<string, unknown>;
  decision: ApiImprovementDecision;
  decision_reason: string | null;
  decided_by_email: string | null;
  decided_at: string | null;
  next_action_url: string | null;
  created_at: string;
  updated_at: string;
}

export function listImprovements(
  workspaceId: string,
  opts: { decision?: ApiImprovementDecision; token?: string } = {},
): Promise<ApiImprovement[]> {
  const qs = opts.decision
    ? `?decision=${encodeURIComponent(opts.decision)}`
    : "";
  return apiFetch<ApiImprovement[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/improvements${qs}`,
    { token: opts.token },
  );
}

export function updateImprovement(
  workspaceId: string,
  improvementId: string,
  patch: {
    decision?: ApiImprovementDecision;
    decision_reason?: string | null;
    next_action_url?: string | null;
  },
  options: { token?: string } = {},
): Promise<ApiImprovement> {
  return apiFetch<ApiImprovement>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/improvements/${encodeURIComponent(improvementId)}`,
    { method: "PATCH", body: patch, token: options.token },
  );
}

// --- Chat threads (C10) ----------------------------------------------------

export interface ApiChatMessage {
  id: string;
  thread_id: string;
  role: "user" | "assistant" | "system";
  body: string;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface ApiChatThread {
  id: string;
  workspace_id: string;
  repo_id: string | null;
  workflow_id: string | null;
  title: string;
  status: "active" | "resolved" | "archived";
  resolved_ticket_ref: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ApiChatThreadDetail extends ApiChatThread {
  messages: ApiChatMessage[];
}

export function listChatThreads(
  workspaceId: string,
  token?: string,
): Promise<ApiChatThread[]> {
  return apiFetch<ApiChatThread[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/threads`,
    { token },
  );
}

export function getChatThread(
  workspaceId: string,
  threadId: string,
  token?: string,
): Promise<ApiChatThreadDetail> {
  return apiFetch<ApiChatThreadDetail>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/threads/${encodeURIComponent(threadId)}`,
    { token },
  );
}

export function createChatThread(
  workspaceId: string,
  payload: {
    title: string;
    initial_message: string;
    repo_id?: string | null;
    workflow_id?: string | null;
  },
  options: { token?: string } = {},
): Promise<ApiChatThreadDetail> {
  return apiFetch<ApiChatThreadDetail>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/threads`,
    { method: "POST", body: payload, token: options.token },
  );
}

export function appendChatMessage(
  workspaceId: string,
  threadId: string,
  body: string,
  options: { token?: string } = {},
): Promise<ApiChatThreadDetail> {
  return apiFetch<ApiChatThreadDetail>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/threads/${encodeURIComponent(threadId)}/messages`,
    { method: "POST", body: { body }, token: options.token },
  );
}

export function resolveChatThread(
  workspaceId: string,
  threadId: string,
  payload: {
    ticket_ref: string;
    create_improvement?: boolean;
    action?: "resolved" | "archived";
  },
  options: { token?: string } = {},
): Promise<ApiChatThreadDetail> {
  return apiFetch<ApiChatThreadDetail>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/threads/${encodeURIComponent(threadId)}/resolve`,
    { method: "POST", body: payload, token: options.token },
  );
}

// ---------------------------------------------------------------------------
// D11 — SHIP-book metrics dashboard
// ---------------------------------------------------------------------------

export type MetricsWindow = "7d" | "30d" | "90d";

export interface ApiMetricsKindCount {
  kind: string;
  total: number;
  succeeded: number;
  failed: number;
  success_rate: number | null;
}

export interface ApiMetricsBucket {
  key: string;
  value: number;
}

export interface ApiMetricsOverview {
  window_days: number;
  window_start: string;
  window_end: string;
  pipelines: {
    total: number;
    enabled: number;
    disabled: number;
    by_kind: ApiMetricsKindCount[];
  };
  runs: {
    total: number;
    succeeded: number;
    failed: number;
    running: number;
    other: number;
    success_rate: number | null;
    avg_duration_seconds: number | null;
    by_kind: ApiMetricsKindCount[];
    by_trigger: ApiMetricsBucket[];
  };
  clarifications: {
    total: number;
    open: number;
    answered: number;
    skipped: number;
    stale: number;
    answer_rate: number | null;
    median_resolution_hours: number | null;
  };
  improvements: {
    total: number;
    pending: number;
    accepted: number;
    declined: number;
    deferred: number;
    accept_rate: number | null;
  };
  chat: {
    threads_total: number;
    threads_active: number;
    threads_resolved: number;
    threads_archived: number;
    messages_total: number;
    ticket_rate: number | null;
  };
  dora: {
    prs_opened: number;
    prs_merged: number;
    deploy_frequency_per_day: number | null;
    avg_lead_time_hours: number | null;
    workflow_runs_total: number;
    workflow_runs_failed: number;
    change_failure_rate: number | null;
    mttr_hours: number | null;
  };
}

/**
 * Single-trip fetch for the ``/metrics`` page. Every panel maps 1:1
 * to a top-level key on the response so the page can render without
 * further aggregation client-side.
 */
export function getMetricsOverview(
  workspaceId: string,
  window: MetricsWindow = "30d",
  options: { token?: string } = {},
): Promise<ApiMetricsOverview> {
  const qs = new URLSearchParams({ window }).toString();
  return apiFetch<ApiMetricsOverview>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/metrics/overview?${qs}`,
    { token: options.token },
  );
}

/**
 * Patch a single activated repo. Today the only mutable field is
 * ``preset``; ``reshape`` tells the backend to re-apply the preset's
 * default enabled-kinds shape to lanes bound to this repo (otherwise
 * we stay additive-only).
 */
export function updateRepo(
  workspaceId: string,
  repoId: string,
  patch: { preset?: string | null; reshape?: boolean },
  options: { token?: string } = {},
): Promise<ApiActivatedRepo> {
  return apiFetch<ApiActivatedRepo>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(
      repoId,
    )}`,
    { method: "PATCH", body: patch, token: options.token },
  );
}

export interface ApiDisconnectRepo {
  repo_id: string;
  full_name: string;
  deleted_pipelines: number;
  deleted_runs: number;
}

/**
 * Delete ``repo_id`` from the workspace. Cascades to pipelines bound
 * to the repo + their runs. Doesn't touch github.com (the App config
 * and any already-committed workflow YAMLs are the operator's to
 * clean up). Idempotent-ish: 404 on a stale id is surfaced as
 * :class:`ApiHttpError` so the UI can auto-refresh and move on.
 */
export function disconnectRepo(
  workspaceId: string,
  repoId: string,
  options: { token?: string } = {},
): Promise<ApiDisconnectRepo> {
  return apiFetch<ApiDisconnectRepo>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(
      repoId,
    )}`,
    { method: "DELETE", token: options.token },
  );
}

/** Response from ``POST /workspaces/{ws}/repos/{id}/install_bundle``. */
export interface ApiInstallBundle {
  pr_url: string;
  pr_number: number;
  branch: string;
  files: string[];
  presets: string[];
}

/**
 * Opens one PR that carries every workflow YAML + ``.ship/config.yml``
 * the selected preset(s) need. Replaces 3-4 sequential single-lane
 * ``installPipelineWorkflow`` calls with a single "Install everything"
 * click. Pass ``presets`` to override the repo's persisted preset.
 */
export function installBundle(
  workspaceId: string,
  repoId: string,
  options: { presets?: string[]; token?: string } = {},
): Promise<ApiInstallBundle> {
  const body: Record<string, unknown> = {};
  if (options.presets && options.presets.length > 0) {
    body.presets = options.presets;
  }
  return apiFetch<ApiInstallBundle>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(
      repoId,
    )}/install_bundle`,
    {
      method: "POST",
      body: Object.keys(body).length === 0 ? undefined : body,
      token: options.token,
    },
  );
}

// --- Pipelines + dashboard (Day-3 main app surface) -----------------------

/**
 * One row of the dashboard's pipeline strip. Mirrors the backend
 * ``PipelineOut``. ``last_run_at`` / ``last_run_status`` are denormalised
 * onto the pipeline so the card can render "last run · ok · 4m ago" with
 * a single SELECT.
 */
export interface ApiPipeline {
  id: string;
  kind: string;
  name: string;
  workflow_id: string;
  enabled: boolean;
  config: Record<string, unknown>;
  last_run_at: string | null;
  last_run_status: string | null;
  created_at: string;
  updated_at: string;
  // Day-4 Phase-1 additions powering the dashboard's three card states
  // (Run-now / Install-workflow / Coming-soon). ``workflow_installed``
  // is null when the kind isn't yet supported by the real executor or
  // when the pipeline isn't bound to a repo; non-null means the
  // backend probed GitHub successfully and ``true``/``false`` reflects
  // whether the starter workflow lives in the customer repo.
  repo_id: string | null;
  repo_full_name: string | null;
  workflow_installed: boolean | null;
  workflow_file: string | null;
  supports_run: boolean;
}

export interface ApiPipelineRun {
  id: string;
  pipeline_id: string;
  trigger: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  summary: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ApiDashboardPullRequest {
  id: string;
  repo_full_name: string;
  number: number;
  title: string;
  state: string;
  merged: boolean;
  draft: boolean;
  author: string | null;
  html_url: string;
  opened_at: string | null;
  updated_at_external: string | null;
  closed_at: string | null;
  merged_at: string | null;
}

export interface ApiDashboardWorkflowRun {
  id: string;
  repo_full_name: string;
  name: string;
  event: string | null;
  status: string;
  conclusion: string | null;
  head_branch: string | null;
  head_sha: string | null;
  actor: string | null;
  html_url: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ApiDashboardCounts {
  active_repos: number;
  enabled_pipelines: number;
  open_pull_requests: number;
  runs_last_24h: number;
}

export interface ApiDashboard {
  counts: ApiDashboardCounts;
  pipelines: ApiPipeline[];
  pull_requests: ApiDashboardPullRequest[];
  workflow_runs: ApiDashboardWorkflowRun[];
  pipeline_runs: ApiPipelineRun[];
}

export function listPipelines(
  workspaceId: string,
  token?: string,
): Promise<ApiPipeline[]> {
  return apiFetch<ApiPipeline[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/pipelines`,
    { token },
  );
}

export function togglePipeline(
  workspaceId: string,
  pipelineId: string,
  enabled: boolean,
  token?: string,
): Promise<ApiPipeline> {
  return apiFetch<ApiPipeline>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/pipelines/${encodeURIComponent(pipelineId)}`,
    { method: "PATCH", body: { enabled }, token },
  );
}

export function runPipeline(
  workspaceId: string,
  pipelineId: string,
  note?: string,
  options?: { repoId?: string | null; token?: string },
): Promise<ApiPipelineRun> {
  const body: Record<string, unknown> = { note: note ?? null };
  if (options?.repoId) body.repo_id = options.repoId;
  return apiFetch<ApiPipelineRun>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/pipelines/${encodeURIComponent(pipelineId)}/runs`,
    { method: "POST", body, token: options?.token },
  );
}

export interface ApiPipelineInstall {
  pr_url: string;
  pr_number: number;
  branch: string;
}

/**
 * POST `/v1/workspaces/{ws}/pipelines/{id}/install` — opens a PR in the
 * bound repo with the starter workflow YAML. Returns the PR URL the
 * dashboard deep-links into. Backend is admin-only.
 */
export function installPipelineWorkflow(
  workspaceId: string,
  pipelineId: string,
  options?: { repoId?: string | null; token?: string },
): Promise<ApiPipelineInstall> {
  const body: Record<string, unknown> = {};
  if (options?.repoId) body.repo_id = options.repoId;
  return apiFetch<ApiPipelineInstall>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/pipelines/${encodeURIComponent(pipelineId)}/install`,
    {
      method: "POST",
      body: Object.keys(body).length > 0 ? body : undefined,
      token: options?.token,
    },
  );
}

export function listPipelineRuns(
  workspaceId: string,
  pipelineId: string,
  limit?: number,
  token?: string,
): Promise<ApiPipelineRun[]> {
  const qs = limit !== undefined ? `?limit=${limit}` : "";
  return apiFetch<ApiPipelineRun[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/pipelines/${encodeURIComponent(pipelineId)}/runs${qs}`,
    { token },
  );
}

export function getDashboard(
  workspaceId: string,
  token?: string,
): Promise<ApiDashboard> {
  return apiFetch<ApiDashboard>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/dashboard`,
    { token },
  );
}

// --- Tracker OAuth (Linear / Notion, Day-2 wow flow) ----------------------

/**
 * Response from `POST /v1/integrations/{linear,notion}/install/start`.
 *
 * The console redirects the browser to `install_url`; the vendor bounces
 * back to the backend's `/install/callback`, which 303s back into the
 * onboarding wizard's `tracker` step on the configured console origin.
 */
export interface ApiTrackerInstallStart {
  install_url: string;
  state: string;
}

export function startLinearInstall(
  workspaceId: string,
  token?: string,
): Promise<ApiTrackerInstallStart> {
  return apiFetch<ApiTrackerInstallStart>(
    `/v1/integrations/linear/install/start?workspace_id=${encodeURIComponent(workspaceId)}`,
    { method: "POST", token },
  );
}

export function startNotionInstall(
  workspaceId: string,
  token?: string,
): Promise<ApiTrackerInstallStart> {
  return apiFetch<ApiTrackerInstallStart>(
    `/v1/integrations/notion/install/start?workspace_id=${encodeURIComponent(workspaceId)}`,
    { method: "POST", token },
  );
}

// --- Artifact repos --------------------------------------------------------

export function listArtifactRepos(
  workspaceId: string,
  token?: string,
): Promise<ApiArtifactRepo[]> {
  return apiFetch<ApiArtifactRepo[]>(
    `/v1/workspaces/${workspaceId}/artifact-repos`,
    { token },
  );
}

export function createArtifactRepo(
  workspaceId: string,
  input: { kind: "workspace" | "project"; url: string; default_branch?: string },
  token?: string,
): Promise<ApiArtifactRepo> {
  return apiFetch<ApiArtifactRepo>(
    `/v1/workspaces/${workspaceId}/artifact-repos`,
    {
      method: "POST",
      body: { default_branch: "main", ...input },
      token,
    },
  );
}

export function deleteArtifactRepo(
  workspaceId: string,
  repoId: string,
  token?: string,
): Promise<void> {
  return apiFetch<void>(
    `/v1/workspaces/${workspaceId}/artifact-repos/${repoId}`,
    { method: "DELETE", token },
  );
}

// --- Knowledge -------------------------------------------------------------

export async function listKnowledgeBuckets(
  workspaceId: string,
  token?: string,
): Promise<ApiKnowledgeBucket[]> {
  const payload = await apiFetch<{ buckets: ApiKnowledgeBucket[] }>(
    `/v1/workspaces/${workspaceId}/knowledge`,
    { token },
  );
  return Array.isArray(payload.buckets) ? payload.buckets : [];
}

export function getKnowledgeBucket(
  workspaceId: string,
  slug: string,
  token?: string,
): Promise<ApiKnowledgeBucket> {
  return apiFetch<ApiKnowledgeBucket>(
    `/v1/workspaces/${workspaceId}/knowledge/${encodeURIComponent(slug)}`,
    { token },
  );
}

// --- API tokens ------------------------------------------------------------

export function mintToken(
  input: {
    name: string;
    workspace_id?: string;
    scopes?: string[];
    ttl_days?: number;
  },
  token?: string,
): Promise<ApiTokenMint> {
  return apiFetch<ApiTokenMint>(`/v1/auth/tokens`, {
    method: "POST",
    body: input,
    token,
  });
}

export function listTokens(token?: string): Promise<ApiTokenInfo[]> {
  return apiFetch<ApiTokenInfo[]>(`/v1/auth/tokens`, { token });
}

export function revokeToken(tokenId: string, token?: string): Promise<void> {
  return apiFetch<void>(`/v1/auth/tokens/${encodeURIComponent(tokenId)}`, {
    method: "DELETE",
    token,
  });
}

// --- Members ---------------------------------------------------------------

export function listMembers(
  workspaceId: string,
  token?: string,
): Promise<ApiMember[]> {
  return apiFetch<ApiMember[]>(`/v1/workspaces/${workspaceId}/members`, {
    token,
  });
}

export function inviteMember(
  workspaceId: string,
  input: { email: string; role: ApiMemberRole; display_name?: string | null },
  token?: string,
): Promise<ApiMember> {
  return apiFetch<ApiMember>(`/v1/workspaces/${workspaceId}/members`, {
    method: "POST",
    body: input,
    token,
  });
}

export function updateMemberRole(
  workspaceId: string,
  memberId: string,
  role: ApiMemberRole,
  token?: string,
): Promise<ApiMember> {
  return apiFetch<ApiMember>(
    `/v1/workspaces/${workspaceId}/members/${encodeURIComponent(memberId)}`,
    { method: "PATCH", body: { role }, token },
  );
}

export function removeMember(
  workspaceId: string,
  memberId: string,
  token?: string,
): Promise<void> {
  return apiFetch<void>(
    `/v1/workspaces/${workspaceId}/members/${encodeURIComponent(memberId)}`,
    { method: "DELETE", token },
  );
}

// --- Audit log -------------------------------------------------------------

export function listAuditLog(
  workspaceId: string,
  opts: { limit?: number; before?: number | null; action?: string | null } = {},
  token?: string,
): Promise<ApiAuditPage> {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.before !== undefined && opts.before !== null)
    params.set("before", String(opts.before));
  if (opts.action) params.set("action", opts.action);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<ApiAuditPage>(
    `/v1/workspaces/${workspaceId}/audit-log${suffix}`,
    { token },
  );
}

// --- Workspace lifecycle ---------------------------------------------------

export function deleteWorkspace(
  workspaceId: string,
  slugConfirmation: string,
  token?: string,
): Promise<void> {
  return apiFetch<void>(`/v1/workspaces/${workspaceId}`, {
    method: "DELETE",
    body: { slug_confirmation: slugConfirmation },
    token,
  });
}

// Re-export the integration kind so route handlers can validate without
// poking at types.ts directly.
export type { ApiIntegrationKind };
