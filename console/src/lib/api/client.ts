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
  ApiArtifactRepo,
  ApiAuditPage,
  ApiIntegration,
  ApiIntegrationKind,
  ApiKnowledgeBucket,
  ApiKnowledgeImportSource,
  ApiKnowledgeIngestionRun,
  ApiMember,
  ApiMemberRole,
  ApiSession,
  ApiTokenInfo,
  ApiTokenMint,
  ApiUser,
  ApiWorkspace,
} from "./types";
import type {
  InboxItemDetail,
  InboxItemEvent,
  InboxListResponse,
  InboxCountsResponse,
  InboxFilterState,
  InboxStatus,
  InboxType,
} from "@/lib/inbox-types";
import type { RoutineScheduleV1 } from "@/lib/routine-schedule-spec";
import { getSessionToken } from "./session";

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
    default_agent_profile?: string;
  },
  token?: string,
): Promise<ApiWorkspace> {
  return apiFetch<ApiWorkspace>(`/v1/workspaces/${workspaceId}`, {
    method: "PATCH",
    body: input,
    token,
  });
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
  /**
   * Snapshot of ``seed_bundle.BUNDLE_VERSION`` written the last time
   * this repo was successfully seeded (install_bundle / wizard_seed).
   * ``null`` means never seeded (fresh activation) or seeded before
   * the column existed — UI surfaces that as "run the wizard".
   */
  installed_bundle_version: string | null;
  /** Current ``BUNDLE_VERSION`` the backend would emit on a re-seed. */
  current_bundle_version: string;
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

/**
 * Resolve an ``owner/repo`` slug to its activated repo row.
 *
 * Used by the ``/r/[owner]/[repo]/...`` legacy redirect-pages
 * (RFC-0010 P1-07) — they need the workspace + repo_id to build
 * the new ``/process?scope=repo&repo=<id>`` style URLs. Picks
 * the first workspace the caller belongs to (matches every other
 * repo-mode page in the console today; multi-workspace UI is
 * deferred). Returns ``null`` on any failure or miss so callers
 * can fall back to a graceful default redirect.
 */
export async function resolveRepoBySlug(
  owner: string,
  repo: string,
  token?: string,
): Promise<{ workspace_id: string; repo_id: string; full_name: string } | null> {
  const target = `${owner}/${repo}`.toLowerCase();
  try {
    const workspaces = await listWorkspaces(token);
    if (workspaces.length === 0) return null;
    const workspace = workspaces[0];
    const repos = await listActivatedRepos(workspace.id, token);
    const match = repos.find((r) => r.full_name.toLowerCase() === target);
    if (!match) return null;
    return {
      workspace_id: workspace.id,
      repo_id: match.id,
      full_name: match.full_name,
    };
  } catch {
    return null;
  }
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

// --- Knowledge seed (one-shot PR of starter markdown) -----------------------

/**
 * Whitelist of knowledge starter slugs the backend catalog ships today.
 * Must stay in lockstep with
 * ``backend.app.services.catalog.KNOWLEDGE_STARTERS``.
 */
export const KNOWLEDGE_STARTERS = ["code-style", "ui-runbook"] as const;
export type KnowledgeStarterSlug = (typeof KNOWLEDGE_STARTERS)[number];

export interface ApiKnowledgeSeedResult {
  pr_url: string;
  pr_number: number;
  branch: string;
  files: string[];
  selection: string[];
}

/**
 * Opens a PR that drops ``.ship/knowledge/<slug>.md`` starter files
 * into the tenant repo. Admin-only on the backend.
 *
 * ``selection === undefined`` ⇒ seed every starter the catalog ships
 * today (matches the wizard's "select all" default). Pass an empty
 * array to get a 412 "empty_knowledge_selection" — useful if you want
 * to force a UX hint rather than a silent no-op.
 */
export function knowledgeSeed(
  workspaceId: string,
  repoId: string,
  options: { selection?: KnowledgeStarterSlug[]; token?: string } = {},
): Promise<ApiKnowledgeSeedResult> {
  const body: Record<string, unknown> = {};
  if (options.selection !== undefined) body.selection = options.selection;
  return apiFetch<ApiKnowledgeSeedResult>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/knowledge_seed`,
    {
      method: "POST",
      body,
      token: options.token,
    },
  );
}

// --- Per-repo tracker binding (Wizard v2 iter 4) ---------------------------

export const TRACKER_KINDS = ["linear", "github", "jira"] as const;
export type TrackerKind = (typeof TRACKER_KINDS)[number];

export interface LinearTeamOption {
  id: string;
  key: string;
  name: string;
}

export interface ApiTrackerBinding {
  repo_id: string;
  kind: TrackerKind | null;
  config: Record<string, unknown>;
  /** ``repo`` = per-repo row. ``workspace`` = inherited default. ``none`` = nothing bound. */
  source: "repo" | "workspace" | "none";
  workspace_default_kind: TrackerKind | null;
  /**
   * Safe-to-expose subset of the workspace-level row's config. Today
   * carries ``team_options`` for Linear (populated by the OAuth
   * callback); empty for kinds without a team picker.
   */
  workspace_default_config?: {
    team_options?: LinearTeamOption[];
  };
  /**
   * ``true`` when the workspace-level OAuth row for the binding's
   * kind has a usable token. The wizard disables Linear/Notion pills
   * when ``false`` so an operator can't save a token-less binding
   * (the backend 412s anyway, but UX-wise we want to point them at
   * "Connect at the workspace level first" up front).
   */
  workspace_oauth_connected?: boolean;
}

export function getRepoTrackerBinding(
  workspaceId: string,
  repoId: string,
  token?: string,
): Promise<ApiTrackerBinding> {
  return apiFetch<ApiTrackerBinding>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/tracker`,
    { token },
  );
}

export function setRepoTrackerBinding(
  workspaceId: string,
  repoId: string,
  body: { kind: TrackerKind; config?: Record<string, unknown> },
  token?: string,
): Promise<ApiTrackerBinding> {
  return apiFetch<ApiTrackerBinding>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/tracker`,
    { method: "PUT", body, token },
  );
}

export function deleteRepoTrackerBinding(
  workspaceId: string,
  repoId: string,
  token?: string,
): Promise<void> {
  return apiFetch<void>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/tracker`,
    { method: "DELETE", token },
  );
}

// --- Per-repo agent secrets (Wizard v2 iter 3) -----------------------------

export interface ApiAgentSecretStatus {
  slug: string;
  label: string;
  secret_name: string | null;
  vendor_url: string | null;
  description: string | null;
  required: boolean;
  present: boolean;
}

export interface ApiAgentSecretCheck {
  repo_id: string;
  agents: ApiAgentSecretStatus[];
}

export function checkAgentSecrets(
  workspaceId: string,
  repoId: string,
  options: { slugs?: string[]; token?: string } = {},
): Promise<ApiAgentSecretCheck> {
  const qs = options.slugs?.length
    ? `?slugs=${encodeURIComponent(options.slugs.join(","))}`
    : "";
  return apiFetch<ApiAgentSecretCheck>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/agent-secrets${qs}`,
    { token: options.token },
  );
}

export interface ApiAgentSecretPush {
  pushed: string[];
  failed: { slug: string; reason: string }[];
}

export function pushAgentSecrets(
  workspaceId: string,
  repoId: string,
  secrets: { slug: string; plaintext: string }[],
  token?: string,
): Promise<ApiAgentSecretPush> {
  return apiFetch<ApiAgentSecretPush>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/agent-secrets`,
    { method: "POST", body: { secrets }, token },
  );
}

// --- Tracker FSM catalog (Wizard v2 iter 7) --------------------------------

export interface ApiFsmState {
  id: string;
  label: string;
  description: string;
  transitions: string[];
}

export interface ApiRepoFsm {
  repo_id: string;
  full_name: string;
  tracker_kind: TrackerKind | null;
  source: "repo" | "workspace" | "none";
  markdown: string;
}

export interface ApiTrackerFsm {
  install_path: string;
  states: ApiFsmState[];
  mapping_hints: Record<string, Record<string, string>>;
  workspace_default_kind: TrackerKind | null;
  repos: ApiRepoFsm[];
}

export function getTrackerFsm(
  workspaceId: string,
  options: { includeRepos?: boolean; token?: string } = {},
): Promise<ApiTrackerFsm> {
  const qs = options.includeRepos === false ? "?repos=false" : "";
  return apiFetch<ApiTrackerFsm>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/tracker-fsm${qs}`,
    { token: options.token },
  );
}

// --- Unified wizard seed PR (Wizard v2 iter 5) -----------------------------

/**
 * CODEOWNERS → routing summary block on :class:`WizardSeedOut` (P5-06).
 *
 * Surfaced in the Wave-8c "what just happened" panel so the operator
 * can see how many routing rules were pre-seeded from CODEOWNERS and
 * which owner handles couldn't be matched to a workspace member yet.
 */
export interface ApiWizardSeedCodeownersSummary {
  file_found: boolean;
  rules_count: number;
  routing_rules_created: number;
  unresolved_owners: string[];
}

/**
 * Repo-intel harvest dispatch handle (P5-06).
 *
 * - ``enqueued=true`` → arq worker is processing the harvest;
 *   ``job_id`` is the polling handle.
 * - ``enqueued=false`` → no worker, the wizard ran the harvest
 *   inline and ``intel_id`` points at the freshly-inserted row.
 */
export interface ApiWizardSeedIntelHandle {
  enqueued: boolean;
  job_id: string | null;
  intel_id: string | null;
}

export interface ApiWizardSeedResult {
  pr_url: string;
  pr_number: number;
  branch: string;
  files: string[];
  presets: string[];
  knowledge_slugs: string[];
  tracker_kind: string | null;
  run_token_prefix: string | null;
  run_token_rotated: boolean;
  // ── P5-06 / P5-07 additions ────────────────────────────────
  // All three default to ``null`` / ``0`` server-side so older FE
  // builds that don't read them keep deserialising. The Wave-8c
  // wizard's done step renders them directly.
  codeowners: ApiWizardSeedCodeownersSummary | null;
  intel: ApiWizardSeedIntelHandle | null;
  synthetic_lanes_created: number;
}

export function wizardSeed(
  workspaceId: string,
  repoId: string,
  body: {
    presets?: string[] | null;
    knowledge_slugs?: KnowledgeStarterSlug[] | null;
    tracker_kind?: TrackerKind | null;
    include_fsm?: boolean;
    rotate_run_token?: boolean;
  },
  token?: string,
): Promise<ApiWizardSeedResult> {
  return apiFetch<ApiWizardSeedResult>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/wizard_seed`,
    { method: "POST", body, token },
  );
}

/**
 * Alias for :type:`ApiWizardSeedResult` — the v2 ``WizardSeedOut``
 * shape returned by both ``POST .../wizard_seed`` and (P5-09)
 * ``GET .../wizard_seed/latest``. Kept under both names so call
 * sites that prefer "Result" (active dispatch) and ones that prefer
 * "Out" (read-back / sessionStorage cache) read naturally.
 */
export type ApiWizardSeedOut = ApiWizardSeedResult;

/**
 * Fetch the most recent ``WizardSeedOut`` for a repo (P5-09).
 *
 * Backs the post-onboarding "What just happened" page when the
 * sessionStorage cache (``ship.wizard_seed_result.<repo_id>``) is
 * empty — typically because the operator reloaded the tab or
 * opened the URL on a different device. Throws an
 * :class:`ApiHttpError` with ``status === 404`` when the repo has
 * never been wizard-seeded; the page treats that as the "no
 * bootstrap yet" empty state, not a generic error.
 */
export function getLatestWizardSeed(
  workspaceId: string,
  repoId: string,
  token?: string,
): Promise<ApiWizardSeedOut> {
  return apiFetch<ApiWizardSeedOut>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/wizard_seed/latest`,
    { token },
  );
}

// --- Wizard seed activation (post-seed-PR auto-merge modal) ---------------

export interface ApiWizardSeedActivateResult {
  merged: boolean;
  pr_number: number;
  sha: string | null;
  message: string | null;
}

/**
 * Merge the wizard seed PR on the operator's behalf via the App's
 * installation token. Powers the "Activate Ship now" modal. Caller
 * passes the ``pr_number`` returned by :func:`wizardSeed`. Branch
 * protection / required checks come back as a 409 ``merge_blocked``;
 * the FE falls back to "I'll merge it myself" without surfacing it
 * as a generic error.
 */
export function activateWizardSeed(
  workspaceId: string,
  repoId: string,
  prNumber: number,
  token?: string,
): Promise<ApiWizardSeedActivateResult> {
  return apiFetch<ApiWizardSeedActivateResult>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/wizard_seed/activate`,
    { method: "POST", body: { pr_number: prNumber }, token },
  );
}

// --- Repo intel (P5-09 — read + manual re-harvest) -------------------------

/**
 * Live ``RepoIntel`` snapshot for ``repo_id`` (P5-09).
 *
 * Mirrors :class:`backend.app.api.v1.routes.repos.RepoIntelOut`. Empty
 * dicts/lists for payload columns mean "harvest succeeded but the
 * extractor found nothing"; the absence of the row entirely surfaces
 * as a 404 from :func:`getCurrentRepoIntel`.
 */
export interface ApiRepoIntel {
  intel_id: string;
  version: number;
  is_current: boolean;
  /** ``{"typescript": 0.62, ...}``. Floats sum to roughly 1. */
  languages: Record<string, number>;
  /** Lowercased canonical framework names, e.g. ``["next.js", "fastapi"]``. */
  frameworks: string[];
  /** Detected from manifest files, e.g. ``["npm", "uv"]``. */
  package_managers: string[];
  /** ``[{"path": "console/src/app/page.tsx", "kind": "page"}, …]``. */
  entry_points: { path?: string; kind?: string; [k: string]: unknown }[];
  /** ``{"top_level_dirs": [...], "depth_p50": 3, "file_count": 1234}``. */
  structure: Record<string, unknown>;
  commit_style: Record<string, unknown>;
  visual_tokens: Record<string, unknown>;
  harvested_at: string;
  harvested_by: string | null;
  harvest_duration_ms: number | null;
  harvest_error: string | null;
}

/**
 * Fetch the live :class:`RepoIntel` snapshot.
 *
 * Throws :class:`ApiHttpError` with ``status === 404`` when no
 * harvest has landed yet — the polling badge on the post-onboarding
 * page swallows 404 and keeps polling instead of surfacing it as
 * an error.
 */
export function getCurrentRepoIntel(
  workspaceId: string,
  repoId: string,
  options: { token?: string; signal?: AbortSignal } = {},
): Promise<ApiRepoIntel> {
  return apiFetch<ApiRepoIntel>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/intel/current`,
    { token: options.token, signal: options.signal },
  );
}

export interface ApiRepoIntelHarvestHandle {
  enqueued: boolean;
  job_id: string | null;
  intel_id: string | null;
}

/**
 * Manually re-trigger the intel harvest (P5-09 retry path).
 *
 * Reuses the wizard's own dispatch helper server-side so the
 * response shape mirrors :type:`ApiWizardSeedIntelHandle` — the FE
 * doesn't need to branch on whether the deployment runs an arq
 * worker or executes the harvest inline.
 */
export function triggerRepoIntelHarvest(
  workspaceId: string,
  repoId: string,
  token?: string,
): Promise<ApiRepoIntelHarvestHandle> {
  return apiFetch<ApiRepoIntelHarvestHandle>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/intel/harvest`,
    { method: "POST", token },
  );
}

/**
 * Canonical Plays bundle preview — ``GET /v1/catalog/default-bundle``.
 *
 * Wave-8c "Confirm bootstrap" wizard step renders this verbatim. The
 * order of ``bundle`` is the recommended display order on the
 * backend (PR-attached first, scheduled scanners next, then
 * release-time + the one-shot knowledge seed) so the FE doesn't
 * re-sort.
 */
export interface ApiDefaultBundleEntry {
  key: string;
  title: string;
  reason: string;
}

export interface ApiDefaultBundle {
  bundle: ApiDefaultBundleEntry[];
}

export function getDefaultBundle(token?: string): Promise<ApiDefaultBundle> {
  return apiFetch<ApiDefaultBundle>(`/v1/catalog/default-bundle`, { token });
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
  /**
   * Outcome of the welcome-email handoff for this invite.
   *
   * - ``"queued"`` — backend accepted the message and handed it to
   *   the email transport in the background.
   * - ``"skipped"`` — ``EMAIL_PROVIDER=none`` is configured; no
   *   email was rendered. Admin should copy the accept URL.
   * - ``"disabled"`` — reserved for future per-workspace opt-outs.
   * - ``null`` — pre-email-feature row, or the field was not set
   *   (e.g. on the list endpoint, which never sends).
   */
  email_status: "queued" | "skipped" | "disabled" | null;
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

export function resendInvite(
  workspaceId: string,
  inviteId: string,
  options: { token?: string } = {},
): Promise<ApiInvite> {
  return apiFetch<ApiInvite>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/invites/${encodeURIComponent(inviteId)}/resend`,
    { method: "POST", token: options.token },
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
  opts: {
    status?: ApiClarificationStatus;
    repoId?: string;
    token?: string;
  } = {},
): Promise<ApiClarification[]> {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.repoId) params.set("repo_id", opts.repoId);
  const qs = params.toString();
  return apiFetch<ApiClarification[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/clarifications${qs ? `?${qs}` : ""}`,
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
  opts: {
    decision?: ApiImprovementDecision;
    repoId?: string;
    token?: string;
  } = {},
): Promise<ApiImprovement[]> {
  const params = new URLSearchParams();
  if (opts.decision) params.set("decision", opts.decision);
  if (opts.repoId) params.set("repo_id", opts.repoId);
  const qs = params.toString();
  return apiFetch<ApiImprovement[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/improvements${qs ? `?${qs}` : ""}`,
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

// --- Chat threads + agent memory (C12) -------------------------------------
//
// The C12 surface collapses the "list of chats" UX into a single
// window: there's one active thread per user per workspace, and
// topic shifts are handled by packing the current thread into a
// named knowledge bucket. The API here is minimal on purpose —
// the streaming turn is routed through ``/api/chat/stream`` (see
// ``console/src/app/api/chat/stream/route.ts``) because Next.js
// server components can't directly expose an SSE socket to the
// browser.

export interface ApiChatMessage {
  id: string;
  thread_id: string;
  role: "user" | "assistant" | "system" | "tool";
  body: string;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface ApiChatThread {
  id: string;
  title: string;
  status: "active" | "archived";
  topic_summary: string | null;
  packed_into_bucket_id: string | null;
  last_user_activity_at: string | null;
  archived_at?: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  messages: ApiChatMessage[];
}

// Trimmed shape returned by ``GET /v1/workspaces/{ws}/chat/threads``;
// the route omits the message transcript so the archive list page
// can render dozens of rows without a per-thread fan-out. Use
// :func:`getActiveChatThread` / a future detail route for the
// full message body.
export interface ApiChatThreadSummary {
  id: string;
  title: string;
  status: "active" | "resolved" | "archived";
  topic_summary: string | null;
  packed_into_bucket_id: string | null;
  last_user_activity_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export function listChatThreads(
  workspaceId: string,
  params: { status?: "active" | "resolved" | "archived"; limit?: number } = {},
  token?: string,
): Promise<ApiChatThreadSummary[]> {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  if (params.limit != null) search.set("limit", String(params.limit));
  const qs = search.toString();
  const path = `/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/threads${
    qs ? `?${qs}` : ""
  }`;
  return apiFetch<ApiChatThreadSummary[]>(path, { token });
}

export function getActiveChatThread(
  workspaceId: string,
  token?: string,
): Promise<ApiChatThread> {
  return apiFetch<ApiChatThread>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/active`,
    { token },
  );
}

export function newActiveChatThread(
  workspaceId: string,
  payload: {
    title?: string | null;
    pack_into_bucket_slug?: string | null;
    pack_into_bucket_name?: string | null;
  } = {},
  options: { token?: string } = {},
): Promise<ApiChatThread> {
  return apiFetch<ApiChatThread>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/active/new`,
    { method: "POST", body: payload, token: options.token },
  );
}

// --- Knowledge buckets ------------------------------------------------------

export interface ApiBucket {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  summary_count: number;
  // Phase 1/5d: scope + source fields surfaced by BucketOut.
  // Optional so we stay tolerant of older backend builds that
  // haven't rolled out the migration yet.
  scope_kind?: import("./types").ApiBucketScope;
  source_kind?: import("./types").ApiBucketSource;
  source_ref?: Record<string, unknown> | null;
  project_id?: string | null;
  repo_id?: string | null;
  user_id?: string | null;
}

export interface ApiBucketSummary {
  id: string;
  bucket_id: string;
  thread_id: string | null;
  title: string;
  summary: string;
  created_at: string;
}

export function listBuckets(
  workspaceId: string,
  opts: { includeArchived?: boolean; token?: string } = {},
): Promise<ApiBucket[]> {
  const qs = opts.includeArchived ? "?include_archived=true" : "";
  return apiFetch<ApiBucket[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets${qs}`,
    { token: opts.token },
  );
}

export function getBucket(
  workspaceId: string,
  slug: string,
  token?: string,
): Promise<ApiBucket> {
  return apiFetch<ApiBucket>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets/${encodeURIComponent(slug)}`,
    { token },
  );
}

export function listBucketSources(
  workspaceId: string,
  slug: string,
  token?: string,
): Promise<import("./types").ApiKnowledgeSource[]> {
  return apiFetch<import("./types").ApiKnowledgeSource[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets/${encodeURIComponent(slug)}/sources`,
    { token },
  );
}

export function listKnowledgeImportSources(
  workspaceId: string,
  token?: string,
): Promise<ApiKnowledgeImportSource[]> {
  return apiFetch<ApiKnowledgeImportSource[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/knowledge/sources`,
    { token },
  );
}

export function createKnowledgeImportSource(
  workspaceId: string,
  payload: {
    kind: string;
    name: string;
    config?: Record<string, unknown>;
    integration_id?: string | null;
    repo_id?: string | null;
    sync_interval_minutes?: number | null;
  },
  options: { token?: string } = {},
): Promise<ApiKnowledgeImportSource> {
  return apiFetch<ApiKnowledgeImportSource>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/knowledge/sources`,
    { method: "POST", body: payload, token: options.token },
  );
}

export function syncKnowledgeImportSource(
  workspaceId: string,
  sourceId: string,
  token?: string,
): Promise<ApiKnowledgeIngestionRun> {
  return apiFetch<ApiKnowledgeIngestionRun>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/knowledge/sources/${encodeURIComponent(sourceId)}/sync`,
    { method: "POST", token },
  );
}

export function archiveBucket(
  workspaceId: string,
  slug: string,
  options: { token?: string } = {},
): Promise<ApiBucket> {
  return apiFetch<ApiBucket>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets/${encodeURIComponent(slug)}/archive`,
    { method: "POST", token: options.token },
  );
}

export function restoreBucket(
  workspaceId: string,
  slug: string,
  options: { token?: string } = {},
): Promise<ApiBucket> {
  return apiFetch<ApiBucket>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets/${encodeURIComponent(slug)}/restore`,
    { method: "POST", token: options.token },
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
  /**
   * RFC-0010 §RunSummary — the structured outcome a pattern authors
   * and persists on ``pipeline_runs.outcome``. Optional in TS so
   * legacy clients / older API builds (or test fixtures hand-rolled
   * before Wave 6 Phase 3 landed) don't break the contract; the
   * canonical shape mirror lives further down this module
   * ({@link RunSummary}).
   */
  outcome?: RunSummary;
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

export interface ApiWorkspaceNotification {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  href: string | null;
  payload: Record<string, unknown>;
  dedupe_key: string | null;
  dismissed_at: string | null;
  created_at: string;
}

export interface ApiDashboard {
  counts: ApiDashboardCounts;
  pipelines: ApiPipeline[];
  pull_requests: ApiDashboardPullRequest[];
  workflow_runs: ApiDashboardWorkflowRun[];
  pipeline_runs: ApiPipelineRun[];
  notifications: ApiWorkspaceNotification[];
}

export type ApiOpsStatus = "ok" | "degraded" | "critical";
export type ApiOpsImpact = "high" | "medium" | "low";

export interface ApiOpsSystemStatus {
  overall_status: ApiOpsStatus;
  failing_pipelines_count: number;
  stuck_prs_count: number;
  broken_automations_count: number;
  last_deploy: { time: string | null; status: string | null } | null;
}

export interface ApiOpsBlocker {
  type: "pipeline" | "pr" | "automation" | "external";
  title: string;
  repo: string | null;
  scope: string | null;
  age_seconds: number;
  impact: ApiOpsImpact;
  href: string | null;
}

export interface ApiOpsWorkItemPrLink {
  number: number;
  href: string;
}

export interface ApiOpsWorkItem {
  name: string;
  status: "in_progress" | "review" | "blocked";
  repo: string | null;
  scope: string | null;
  updated_at: string;
  blocker_ref: string | null;
  href: string | null;
  ticket_ref?: string | null;
  tracker?: string | null;
  board_column?: string | null;
  active_agent?: string | null;
  pull_request?: ApiOpsWorkItemPrLink | null;
}

export interface ApiOpsShippedItem {
  name: string;
  type: "feature" | "fix" | "rollback";
  repo: string | null;
  href: string | null;
}

export interface ApiOpsShipped {
  features_shipped_count: number;
  fixes_count: number;
  rollbacks_count: number;
  items: ApiOpsShippedItem[];
}

export interface ApiOpsBottleneck {
  metric: string;
  current_value: string;
  delta: string | null;
  severity: ApiOpsImpact;
}

export interface ApiOpsAutomationHealth {
  automation_coverage: number | null;
  success_rate: number | null;
  manual_interventions_count: number;
  failures_count: number;
}

export interface ApiOpsSuggestedAction {
  action: string;
  reason: string;
  priority: ApiOpsImpact;
  href: string | null;
}

export interface ApiOpsDashboard {
  system_status: ApiOpsSystemStatus;
  blockers: ApiOpsBlocker[];
  work_in_progress: ApiOpsWorkItem[];
  shipped: ApiOpsShipped;
  bottlenecks: ApiOpsBottleneck[];
  automation_health: ApiOpsAutomationHealth;
  suggested_actions: ApiOpsSuggestedAction[];
}

// --- Process orchestration ---------------------------------------------------

export type ApiProcessHealth = "ok" | "degraded" | "failed";
export type ApiProcessTaskStatus = "active" | "blocked" | "done";
export type ApiProcessLinkType =
  | "handoff"
  | "dependency"
  | "approval"
  | "notification";

export interface ApiProcessCondition {
  expression: string;
}

export interface ApiProcessTrigger {
  type: "schedule" | "event" | "manual";
  interval: string | null;
  event: string | null;
}

export interface ApiProcessScheduleSlot {
  id: string;
  local_time: string;
  weekdays: number[];
  specialist_ids: string[];
  label?: string | null;
}

export interface ApiProcessSchedule {
  trigger?: {
    kind: "schedule" | "event" | "manual";
    event?: string | null;
  } | null;
  time_zone: string;
  slots: ApiProcessScheduleSlot[];
}

export interface ApiProcessStateRuntime {
  task_count: number;
  blocked_count: number;
  last_execution_time: string | null;
  health: ApiProcessHealth;
}

// CanonicalState / CANONICAL_STATES / TRACKER_OVERLAY now live in
// ``@/lib/api/types`` so client components can import them without
// dragging in this server-only module. Re-exported here for backward
// compatibility with existing import paths.
export {
  CANONICAL_STATES,
  TRACKER_OVERLAY,
  type CanonicalState,
} from "@/lib/api/types";

/**
 * A *stage* in a process — work step the operator sees on the canvas.
 *
 * Despite the type name (kept stable for now to avoid a cross-file
 * rename), each entry is conceptually a "stage": a discrete step where
 * a specialist does something. ``state`` buckets the stage into one
 * of the seven canonical lifecycle phases.
 */
export interface ApiProcessState {
  id: string;
  name: string;
  specialist_id: string;
  specialist_name: string;
  instructions: string;
  /** Canonical lifecycle bucket this stage lives in. */
  state: CanonicalStateRef;
  layout?: {
    x: number;
    y: number;
  } | null;
  triggers: ApiProcessTrigger[];
  exit_conditions: ApiProcessCondition[];
  block_conditions: ApiProcessCondition[];
  runtime: ApiProcessStateRuntime;
}
// Local alias just so the field type below references the re-exported
// CanonicalState without TypeScript flagging a self-reference cycle.
type CanonicalStateRef = import("@/lib/api/types").CanonicalState;

// Re-exported from types so client components can import from here
// without server-only contamination.
export { type TransitionActor } from "@/lib/api/types";

export interface ApiProcessTransition {
  id: string;
  from_state_id: string;
  to_state_id: string;
  conditions: ApiProcessCondition[];
  /** When true, the transition needs an explicit human action in the console before it can fire. */
  requires_human?: boolean;
  /**
   * Who advances the ticket along this transition. Default ``agent``
   * (intra-bucket handoffs); ``user`` for human-gated arrows like
   * Backlog → Todo or Review → Done; ``either`` when both paths are
   * valid (overlay-state resume).
   */
  trigger_actor?: import("@/lib/api/types").TransitionActor;
}

export interface ApiProcessSpecialist {
  id: string;
  name: string;
  role: string;
  capabilities: string[];
  agent_profile: string;
  version?: string | null;
  source?: "ship_managed" | "workspace_custom" | "process" | string;
}

export interface ApiProcessRoleTemplate {
  id: string;
  name: string;
  description: string;
  prompt_template: string;
  capabilities: string[];
  default_agent_profile: string;
  version: string;
  source: "ship_managed" | "workspace_custom" | string;
  default_phases: string[];
}

export interface ApiProcessTask {
  id: string;
  title: string;
  state_id: string;
  status: ApiProcessTaskStatus;
  last_updated: string | null;
  context: Record<string, unknown>;
  blockers: string[];
}

export interface ApiProcessRoutine {
  id: string;
  name: string;
  specialist_id: string;
  specialist_name: string;
  schedule: string | null;
  /** Agent prompt; persisted as `prompt` in .ship/config.yml. */
  prompt?: string;
  /**
   * @deprecated API mirror of prompt for older projections; use `prompt`.
   */
  instructions?: string;
  last_run: string | null;
  status: string | null;
  /** When false, routine is declared but should not run (from repo process config). */
  enabled?: boolean;
  /** Optional human summary for cards; if omitted at save, derived from `prompt`. */
  description?: string;
  /** Routine execution trigger from `process.routines`; never used for ticket picking. */
  trigger?: Record<string, unknown> | null;
  /** Read-only context contract such as repos, commits, PRs, runs, and lookback window. */
  scope?: Record<string, unknown> | null;
  /** Typed routine output destination, e.g. inbox, digest, or tracker_comment. */
  output?: Record<string, unknown> | null;
  /** Optional backend-managed prompt metadata for generated/versioned routine prompts. */
  prompt_record?: {
    id: string;
    version: number;
    source: "ai_draft" | "manual" | "seed" | string;
    assumptions?: string[];
  } | null;
  /**
   * Editor state from YAML `schedule` block; used to rehydrate the schedule UI.
   */
  schedule_spec?: RoutineScheduleV1 | null;
}

export interface ApiProcessLink {
  id: string;
  from_process_id: string;
  from_state_id: string | null;
  from_node_id?: string | null;
  to_process_id: string;
  to_state_id: string | null;
  to_node_id?: string | null;
  type: ApiProcessLinkType;
  conditions: ApiProcessCondition[];
  label?: string | null;
  io_contract?: Record<string, unknown>;
}

export type ApiProcessNodeType =
  | "workspace"
  | "process"
  | "subprocess"
  | "routine"
  | "approval";

export interface ApiProcessNode {
  id: string;
  process_id: string;
  type: ApiProcessNodeType;
  name: string;
  description: string;
  parent_process_id: string | null;
  child_process_id?: string | null;
  template_id?: string | null;
  x: number;
  y: number;
  status: ApiProcessHealth;
}

export interface ApiProcessGraph {
  nodes: ApiProcessNode[];
  links: ApiProcessLink[];
}

export interface ApiProcessSummary {
  id: string;
  name: string;
  primary: boolean;
  state_count: number;
  task_count: number;
  blocked_count: number;
  health: ApiProcessHealth;
  description?: string;
  parent_process_id?: string | null;
  node_type?: ApiProcessNodeType;
  template_id?: string | null;
}

export interface ApiProcessAdapterDiagnostic {
  kind: "tracker" | "runner" | "agent";
  name: string;
  status: "ok" | "degraded" | "not_configured" | "unknown";
  message: string;
  capabilities: string[];
  missing_mappings?: string[];
}

export interface ApiProcessList {
  primary_process_id: string;
  processes: ApiProcessSummary[];
  process_graph: ApiProcessGraph;
  adapter_diagnostics: ApiProcessAdapterDiagnostic[];
}

export interface ApiProcess extends ApiProcessSummary {
  specialists: ApiProcessSpecialist[];
  states: ApiProcessState[];
  transitions: ApiProcessTransition[];
  tasks: ApiProcessTask[];
  routines: ApiProcessRoutine[];
  schedule?: ApiProcessSchedule | null;
  process_graph: ApiProcessGraph;
  adapter_diagnostics: ApiProcessAdapterDiagnostic[];
}

export function listProcesses(
  workspaceId: string,
  token?: string,
): Promise<ApiProcessList> {
  return apiFetch<ApiProcessList>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/processes`,
    { token },
  );
}

export function getProcess(
  workspaceId: string,
  processId: string,
  token?: string,
  options: { repoId?: string } = {},
): Promise<ApiProcess> {
  const query = options.repoId
    ? `?repo_id=${encodeURIComponent(options.repoId)}`
    : "";
  return apiFetch<ApiProcess>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/processes/${encodeURIComponent(processId)}${query}`,
    { token },
  );
}

export function listProcessRoleTemplates(
  workspaceId: string,
  token?: string,
): Promise<ApiProcessRoleTemplate[]> {
  return apiFetch<ApiProcessRoleTemplate[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/processes/role-templates`,
    { token },
  );
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
// ---------------------------------------------------------------------
// Lanes (RFC-0007 Phase 7)
// ---------------------------------------------------------------------

/**
 * One row on the Console `/lanes` page. Mirrors the backend
 * `LaneOut` schema from `backend.app.api.v1.routes.lanes`.
 */
export interface ApiLane {
  id: string;
  workspace_id: string;
  repo_id: string;
  repo_full_name: string;
  lane_id: string;
  kind: "once" | "event" | "schedule";
  pattern: string | null;
  cron: string | null;
  idempotency_key: string | null;
  enabled: boolean;
  config: Record<string, unknown>;
  last_run_at: string | null;
  last_run_status: string | null;
  synced_at: string;
  sync_source: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiLaneRun {
  id: string;
  pipeline_id: string;
  status: string;
  trigger: string;
  summary: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ApiLaneDetail extends ApiLane {
  recent_runs: ApiLaneRun[];
}

export interface ApiLaneSyncResult {
  repo_id: string;
  added: number;
  updated: number;
  removed: number;
  unchanged: number;
  errors: string[];
  sync_source: string | null;
}

/**
 * Built-in lane recipes exposed by `/v1/catalog/lanes`.
 *
 * Mirrors ``DefaultPipelineSpec`` from
 * ``backend/app/services/default_pipelines.py`` but filtered to
 * entries that actually land in ``.ship/config.yml`` (i.e.
 * ``lane_trigger is not None``). Resolver-only specs such as
 * ``code_map`` are omitted server-side because they aren't
 * user-installable lanes; the installer wires them implicitly.
 */
export interface ApiLaneCatalogEntry {
  kind: string;
  title: string;
  summary: string;
  workflow_id: string;
  default_enabled: boolean;
  event: string | null;
  pattern: string | null;
  schedule: string | null;
  idempotency_key: string | null;
}

/**
 * Live ``.ship/config.yml`` surfaced by the Library editor.
 *
 * ``sha`` is the vendor blob SHA; the write endpoint requires it
 * (``base_sha``) for optimistic locking. ``exists === false`` means
 * the editor should post ``base_sha: null`` to create the file from
 * scratch.
 */
export interface ApiRepoConfig {
  repo_id: string;
  repo_full_name: string;
  default_branch: string;
  exists: boolean;
  sha: string | null;
  raw_yaml: string | null;
  parsed: {
    version?: number;
    preset?: string;
    repo?: string;
    lanes?: Record<string, Record<string, unknown>>;
    process?: unknown;
  } | null;
  parse_error: string | null;
}

export function getRepoConfig(
  workspaceId: string,
  repoId: string,
  token?: string,
): Promise<ApiRepoConfig> {
  return apiFetch<ApiRepoConfig>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/config`,
    { token },
  );
}

/**
 * One lane entry as the write endpoint wants to receive it.
 *
 * ``patterns`` (list) is the RFC-0008 C3.1 canonical form; ``pattern``
 * (scalar) stays as a single-pattern alias. Senders use one or the
 * other — never both — and the backend round-trips by emitting
 * ``patterns:`` only when the list has ≥2 entries.
 *
 * ``fanout`` only applies to multi-pattern lanes (RFC-0008 C3.2).
 * ``matrix`` (default), ``sequential``, ``concurrent``. The backend
 * omits it from the YAML when it's the default or when the lane has
 * a single pattern.
 */
export interface ApiLaneTriggerIn {
  once?: string | null;
  event?: string | null;
  schedule?: string | null;
  pattern?: string | null;
  patterns?: string[] | null;
  fanout?: string | null;
  idempotency_key?: string | null;
}

export interface ApiRepoConfigProposeIn {
  lanes: Record<string, ApiLaneTriggerIn>;
  process?: unknown;
  base_sha: string | null;
  change_summary?: string;
  preset?: string | null;
}

export interface ApiRepoConfigProposeOut {
  pr_url: string;
  pr_number: number;
  branch: string;
}

export function proposeRepoConfig(
  workspaceId: string,
  repoId: string,
  body: ApiRepoConfigProposeIn,
  token?: string,
): Promise<ApiRepoConfigProposeOut> {
  return apiFetch<ApiRepoConfigProposeOut>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/config/propose`,
    { method: "POST", token, body },
  );
}


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

/**
 * Latest pipeline run per "play key" (RFC-0010 / Wave 7 Phase 4 P4-03).
 *
 * Drives the "Last run for this play in this workspace" mini-strip
 * each ``PlayCard`` shows below its CTAs. The strip is keyed off the
 * pattern id (which mirrors ``pipeline.kind`` for every play we
 * register) so we can look it up in O(1) at render time.
 *
 * **N+1 mitigation strategy.** The backend has no batched
 * "latest-run-per-pipeline" endpoint yet. Two viable shapes:
 *
 *   1. ``getDashboard`` returns ``pipeline_runs`` for the workspace
 *      but only inside a 24-hour window; that loses every play
 *      whose last run was older than a day, which is the common
 *      case for scheduled scanners.
 *   2. Fan-out across pipelines via ``listPipelineRuns(.., 1)``.
 *      Bounded by the number of pipelines registered in the
 *      workspace — small for the pilot tenant and parallelisable
 *      via ``Promise.all``.
 *
 * We pick (2) because correctness > round-trip count for a UX
 * surface where "last run never" is a meaningful state (it's how an
 * operator notices a play hasn't been wired yet). Once the BE adds
 * ``GET /v1/workspaces/{ws}/runs/latest-by-play`` (TODO P4-?: see
 * planning doc) we can replace this loop with a single fetch.
 */
export type LatestRunForPlay = {
  /** Pipeline kind (matches the catalog pattern id). */
  playKey: string;
  /** The most-recent run row for that play in the workspace. */
  run: ApiPipelineRun;
  /** Parent pipeline id — needed to drill into the legacy run-detail URL. */
  pipelineId: string;
};

export async function listLatestRunsByPlay(
  workspaceId: string,
  token?: string,
): Promise<Map<string, LatestRunForPlay>> {
  let pipelines: ApiPipeline[];
  try {
    pipelines = await listPipelines(workspaceId, token);
  } catch {
    return new Map();
  }
  const recents = await Promise.all(
    pipelines.map(async (p) => {
      try {
        const runs = await listPipelineRuns(workspaceId, p.id, 1, token);
        return runs[0] ?? null;
      } catch {
        return null;
      }
    }),
  );
  const out = new Map<string, LatestRunForPlay>();
  pipelines.forEach((p, i) => {
    const run = recents[i];
    if (!run) return;
    const key = p.kind;
    const existing = out.get(key);
    const candidateTs = new Date(
      run.started_at ?? run.created_at,
    ).getTime();
    if (existing) {
      const existingTs = new Date(
        existing.run.started_at ?? existing.run.created_at,
      ).getTime();
      if (candidateTs <= existingTs) return;
    }
    out.set(key, { playKey: key, run, pipelineId: p.id });
  });
  return out;
}

/** GET `/v1/workspaces/{ws}/pipelines/{id}/runs/{runId}` — single run for the detail page. */
export function getPipelineRun(
  workspaceId: string,
  pipelineId: string,
  runId: string,
  token?: string,
): Promise<ApiPipelineRun> {
  return apiFetch<ApiPipelineRun>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/pipelines/${encodeURIComponent(pipelineId)}/runs/${encodeURIComponent(runId)}`,
    { token },
  );
}

// ---------------------------------------------------------------------
// RFC-0010 §RunSummary + run-detail surface (P3-04 / P3-05)
// ---------------------------------------------------------------------

/**
 * RFC-0010 §RunSummary contract — the structured outcome a pattern
 * authors and persists on ``pipeline_runs.outcome``.
 *
 * Mirrors the Pydantic ``RunSummary`` shape declared in
 * ``backend/app/api/v1/routes/pipelines.py``. Sibling subagent C
 * is also extending {@link ApiPipelineRun} to carry an
 * ``outcome: RunSummary`` field — once that lands, this type
 * remains the canonical client-side mirror.
 */
export type RunSummaryArtifact = {
  type: string;
  title: string;
  ref?: string | null;
};

export type RunSummaryFindingsBySeverity = {
  low?: number;
  medium?: number;
  high?: number;
  critical?: number;
};

export type RunSummaryEscalationHint = {
  type: "clarification" | "improvement" | "failure" | "approval" | "exception";
  reason: string;
};

export type RunSummary = {
  outcome_text?: string | null;
  headline?: string | null;
  findings_count?: number | null;
  findings_by_severity?: RunSummaryFindingsBySeverity | null;
  artifacts?: RunSummaryArtifact[];
  requires_approval?: boolean;
  approval_payload?: Record<string, unknown>;
  escalations?: RunSummaryEscalationHint[];
};

/**
 * Detail-view extension of {@link ApiPipelineRun}. Once sibling C's
 * client.ts changes land, ``ApiPipelineRun`` itself will carry
 * ``outcome`` + ``lane_id`` and this alias collapses to the base
 * type. Until then, the optional fields keep us forward-compatible
 * without forcing every other call-site to widen.
 */
export type ApiPipelineRunWithOutcome = ApiPipelineRun & {
  outcome?: RunSummary;
  lane_id?: string | null;
};

/**
 * Bundle returned by {@link getRunDetail}: the run row plus the
 * parent {@link ApiPipeline}. The pipeline ships alongside so the
 * detail page can render the play / lane / repo metadata without
 * a second client hop. ``pipeline`` is nullable because the run
 * may belong to a pipeline that's been disabled or deleted while
 * the row itself survives.
 */
export type RunDetail = {
  run: ApiPipelineRunWithOutcome;
  pipeline: ApiPipeline | null;
};

/**
 * One row from ``/v1/workspaces/{ws}/runs/{run_id}/escalations``.
 *
 * **Backend endpoint TODO** (P3-05-BE): the route does not yet
 * exist server-side. The frontend assumes the join shape declared
 * here — ``inbox_item`` denormalised onto each escalation — so the
 * detail page never has to fan out a second GET per row. Until the
 * backend lands, {@link listRunEscalations} catches the 404 and
 * returns ``[]``.
 */
export type ApiRunEscalation = {
  id: string;
  run_id: string;
  inbox_item_id: string;
  escalation_reason: string;
  created_at: string;
  /** Joined inbox_items projection. May be ``null`` if the linked item was deleted. */
  inbox_item: {
    id: string;
    type: "clarification" | "improvement" | "failure" | "approval" | "exception";
    title: string;
    status: string;
    owner:
      | {
          user_id: string;
          email: string;
          display_name: string | null;
        }
      | null;
  } | null;
};

/**
 * Resolve a run by id alone. The legacy detail endpoint is keyed by
 * ``(workspaceId, pipelineId, runId)`` — there is no
 * "find run by id alone" route yet (see
 * ``backend/app/api/v1/routes/pipelines.py``). We resolve the
 * parent ``pipelineId`` server-side by listing pipelines and
 * scanning each pipeline's recent runs for the matching ``runId``.
 *
 * O(pipelines × runs) and fine for the pilot tenant; once the
 * backend exposes ``GET /v1/workspaces/{ws}/runs/{runId}`` (tracked
 * separately) we can replace the loop with a single fetch.
 *
 * Returns ``null`` when no pipeline in the workspace records a run
 * with that id in the recent window. Callers render a 404 card.
 */
export async function getRunDetail(
  workspaceId: string,
  runId: string,
  token?: string,
): Promise<RunDetail | null> {
  const pipelines = await listPipelines(workspaceId, token);
  for (const pipeline of pipelines) {
    let runs: ApiPipelineRun[];
    try {
      runs = await listPipelineRuns(workspaceId, pipeline.id, 50, token);
    } catch {
      continue;
    }
    if (runs.some((r) => r.id === runId)) {
      const run = await getPipelineRun(
        workspaceId,
        pipeline.id,
        runId,
        token,
      );
      return { run: run as ApiPipelineRunWithOutcome, pipeline };
    }
  }
  return null;
}

/**
 * GET ``/v1/workspaces/{ws}/runs/{run_id}/escalations`` — pull the
 * authoritative ``run_escalations`` rows joined with their target
 * ``inbox_items``. The endpoint is **not yet implemented** (P3-05
 * scoped a frontend-only ticket); a graceful 404 fallback returns
 * an empty list so the run-detail page renders without escalations
 * until the backend lands.
 */
export async function listRunEscalations(
  workspaceId: string,
  runId: string,
  token?: string,
): Promise<ApiRunEscalation[]> {
  try {
    return await apiFetch<ApiRunEscalation[]>(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}/escalations`,
      { token },
    );
  } catch (err) {
    // TODO(P3-05-BE): drop this fallback once
    // ``GET /v1/workspaces/{ws}/runs/{run_id}/escalations`` ships.
    if (err instanceof ApiHttpError && err.status === 404) return [];
    throw err;
  }
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

export function getOpsDashboard(
  workspaceId: string,
  token?: string,
): Promise<ApiOpsDashboard> {
  return apiFetch<ApiOpsDashboard>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/dashboard/ops`,
    { token },
  );
}

export function dismissNotification(
  workspaceId: string,
  notificationId: string,
  token?: string,
): Promise<void> {
  return apiFetch<void>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/notifications/${encodeURIComponent(notificationId)}/dismiss`,
    { method: "POST", token },
  );
}

export function dismissAllNotifications(
  workspaceId: string,
  token?: string,
): Promise<void> {
  return apiFetch<void>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/notifications/dismiss-all`,
    { method: "POST", token },
  );
}

// --- Per-repo Ship-managed secrets (B10) ----------------------------------

/**
 * Plaintext-free projection of a Ship-managed repo secret. The
 * backend never returns the original value once written; the UI
 * relies on {@link ApiRepoSecret.masked_hint} (last 4 plaintext
 * characters, pre-computed at write time) for the familiar
 * "•••••••abcd" display so operators can eyeball whether a key was
 * actually rotated.
 */
export interface ApiRepoSecret {
  id: string;
  name: string;
  masked_hint: string | null;
  description: string | null;
  sync_status: "pending" | "synced" | "stale" | "error" | string;
  sync_error: string | null;
  last_synced_at: string | null;
  github_key_id: string | null;
  created_by_user_id: string | null;
  updated_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * One row of the "required secrets" matrix — the dashboard renders
 * these as warnings when ``stored=false`` so operators can add a
 * missing key before the next cron fires.
 */
export interface ApiRequiredSecret {
  name: string;
  required_by: string[];
  stored: boolean;
  sync_status: string | null;
}

export function listRepoSecrets(
  workspaceId: string,
  repoId: string,
  token?: string,
): Promise<{ items: ApiRepoSecret[] }> {
  return apiFetch<{ items: ApiRepoSecret[] }>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/secrets`,
    { token },
  );
}

export function listRequiredSecrets(
  workspaceId: string,
  repoId: string,
  token?: string,
): Promise<{ items: ApiRequiredSecret[] }> {
  return apiFetch<{ items: ApiRequiredSecret[] }>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/secrets/required`,
    { token },
  );
}

export function upsertRepoSecret(
  workspaceId: string,
  repoId: string,
  payload: { name: string; value: string; description?: string | null },
  token?: string,
): Promise<ApiRepoSecret> {
  return apiFetch<ApiRepoSecret>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/secrets`,
    { method: "POST", body: payload, token },
  );
}

export function deleteRepoSecret(
  workspaceId: string,
  repoId: string,
  secretId: string,
  token?: string,
): Promise<void> {
  return apiFetch<void>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/secrets/${encodeURIComponent(secretId)}`,
    { method: "DELETE", token },
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

// --- Native integrations ----------------------------------------------------

export interface ApiNativeIntegration {
  id: string;
  workspace_id: string;
  provider: string;
  auth_mode: string;
  external_account_id: string;
  external_account_name: string | null;
  external_account_url: string | null;
  capabilities: string[];
  scopes: string[];
  config: Record<string, unknown>;
  status: string;
  has_credential: boolean;
  last_health_at: string | null;
  last_health_error: string | null;
  connected_at: string | null;
  disabled_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiNativeResource {
  resource_type: string;
  external_id: string;
  display_name: string;
  external_url: string | null;
  provider: string;
  config: Record<string, unknown>;
  bound: boolean;
}

export interface ApiNativeBinding {
  id: string;
  installation_id: string;
  provider: string;
  resource_type: string;
  external_id: string;
  display_name: string;
  external_url: string | null;
  config: Record<string, unknown>;
  status: string;
  last_synced_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiNativeCiRun {
  id: string | number | null;
  name: string | null;
  status: string | null;
  conclusion: string | null;
  url: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
  ref?: string | null;
  sha?: string | null;
  source_branch?: string | null;
  source_version?: string | null;
}

export function listNativeIntegrations(
  workspaceId: string,
  token?: string,
): Promise<ApiNativeIntegration[]> {
  return apiFetch<ApiNativeIntegration[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations`,
    { token },
  );
}

export function listNativeRepoResources(
  workspaceId: string,
  installationId: string,
  token?: string,
): Promise<ApiNativeResource[]> {
  return apiFetch<ApiNativeResource[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/${encodeURIComponent(installationId)}/resources/repos`,
    { token },
  );
}

export function listNativeBindings(
  workspaceId: string,
  installationId: string,
  token?: string,
): Promise<ApiNativeBinding[]> {
  return apiFetch<ApiNativeBinding[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/${encodeURIComponent(installationId)}/bindings`,
    { token },
  );
}

export function replaceNativeBindings(
  workspaceId: string,
  installationId: string,
  input: { resource_type?: string; external_ids: string[] },
  token?: string,
): Promise<ApiNativeBinding[]> {
  return apiFetch<ApiNativeBinding[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/${encodeURIComponent(installationId)}/bindings`,
    { method: "PUT", body: input, token },
  );
}

export function listNativeCiRuns(
  workspaceId: string,
  installationId: string,
  bindingId: string,
  limit = 25,
  token?: string,
): Promise<ApiNativeCiRun[]> {
  return apiFetch<ApiNativeCiRun[]>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/${encodeURIComponent(installationId)}/bindings/${encodeURIComponent(bindingId)}/ci/runs?limit=${encodeURIComponent(String(limit))}`,
    { token },
  );
}

export function rerunNativeCiRun(
  workspaceId: string,
  installationId: string,
  bindingId: string,
  runId: string,
  token?: string,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/${encodeURIComponent(installationId)}/bindings/${encodeURIComponent(bindingId)}/ci/runs/${encodeURIComponent(runId)}/rerun`,
    { method: "POST", token },
  );
}

export function getNativeCiLogs(
  workspaceId: string,
  installationId: string,
  bindingId: string,
  runId: string,
  token?: string,
): Promise<{ run_id: string; logs: string }> {
  return apiFetch<{ run_id: string; logs: string }>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/${encodeURIComponent(installationId)}/bindings/${encodeURIComponent(bindingId)}/ci/runs/${encodeURIComponent(runId)}/logs`,
    { token },
  );
}

export function deleteNativeIntegration(
  workspaceId: string,
  installationId: string,
  token?: string,
): Promise<void> {
  return apiFetch<void>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/${encodeURIComponent(installationId)}`,
    { method: "DELETE", token },
  );
}

export function probeNativeIntegration(
  workspaceId: string,
  installationId: string,
  token?: string,
): Promise<ApiNativeIntegration> {
  return apiFetch<ApiNativeIntegration>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/${encodeURIComponent(installationId)}/probe`,
    { method: "POST", token },
  );
}

export function connectAtlassianApiToken(
  workspaceId: string,
  input: {
    site: string;
    email: string;
    api_token: string;
    jira_project?: string | null;
    scopes?: string[];
  },
  token?: string,
): Promise<ApiNativeIntegration> {
  return apiFetch<ApiNativeIntegration>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/atlassian/api-token`,
    { method: "POST", body: input, token },
  );
}

export function connectAzureDevOpsPat(
  workspaceId: string,
  input: {
    organization: string;
    project?: string | null;
    pat: string;
    scopes?: string[];
  },
  token?: string,
): Promise<ApiNativeIntegration> {
  return apiFetch<ApiNativeIntegration>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/azure-devops/pat`,
    { method: "POST", body: input, token },
  );
}

export function connectGitLabPat(
  workspaceId: string,
  input: {
    host: string;
    group?: string | null;
    pat: string;
    scopes?: string[];
  },
  token?: string,
): Promise<ApiNativeIntegration> {
  return apiFetch<ApiNativeIntegration>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/gitlab/pat`,
    { method: "POST", body: input, token },
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

// --- Workspace vector search ---------------------------------------------

export type ApiKnowledgeSearchHit = {
  id: string;
  source: "bucket_article" | "kb_chunk";
  bucket_slug: string | null;
  bucket_id: string | null;
  repo_id: string | null;
  scope_kind: "workspace" | "project" | "repo" | "user";
  score: number;
  rank_bucket: "repo_match" | "workspace" | "other_repo";
  snippet: string;
  title: string | null;
  repo_full_name: string | null;
};

export type ApiKnowledgeSearchResponse = {
  query: string;
  hits: ApiKnowledgeSearchHit[];
};

export function searchKnowledge(
  workspaceId: string,
  payload: {
    query: string;
    repoId?: string | null;
    bucketSlug?: string | null;
    limit?: number;
  },
  token?: string,
): Promise<ApiKnowledgeSearchResponse> {
  return apiFetch<ApiKnowledgeSearchResponse>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/knowledge/search`,
    {
      method: "POST",
      body: {
        query: payload.query,
        repo_id: payload.repoId ?? null,
        bucket_slug: payload.bucketSlug ?? null,
        limit: payload.limit ?? 20,
      },
      token,
    },
  );
}

// --- Bucket article listing ----------------------------------------------

export async function listBucketArticles(
  workspaceId: string,
  slug: string,
  opts: {
    includeSuperseded?: boolean;
    includeArchived?: boolean;
  } = {},
  token?: string,
): Promise<import("./types").ApiBucketArticle[]> {
  const qs = new URLSearchParams();
  if (opts.includeSuperseded) qs.set("include_superseded", "true");
  if (opts.includeArchived) qs.set("include_archived", "true");
  const suffix = qs.size ? `?${qs.toString()}` : "";
  const payload = await apiFetch<import("./types").ApiBucketArticle[]>(
    `/v1/workspaces/${workspaceId}/buckets/${encodeURIComponent(slug)}/articles${suffix}`,
    { token },
  );
  return Array.isArray(payload) ? payload : [];
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

export async function listMembers(
  workspaceId: string,
  token?: string,
): Promise<ApiMember[]> {
  const rows = await apiFetch<ApiMember[]>(`/v1/workspaces/${workspaceId}/members`, {
    token,
  });
  return rows.map((m) => ({
    ...m,
    answer_specialist_slugs: m.answer_specialist_slugs ?? [],
  }));
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

export function patchMember(
  workspaceId: string,
  memberId: string,
  body: { role?: ApiMemberRole; answer_specialist_slugs?: string[] },
  token?: string,
): Promise<ApiMember> {
  return apiFetch<ApiMember>(
    `/v1/workspaces/${workspaceId}/members/${encodeURIComponent(memberId)}`,
    { method: "PATCH", body, token },
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

// --- Inbox: list / detail / disposition (RFC-0010 §5) ---------------------

export type InboxListQuery = {
  ownership?: InboxFilterState["ownership"];
  types?: InboxType[];
  /** Optional; omitted uses API default. */
  statuses?: InboxStatus[];
  repo_id?: string;
  play_key?: string;
  cursor?: string | null;
  limit?: number;
};

function buildInboxQuery(opts: InboxListQuery): string {
  const params = new URLSearchParams();
  if (opts.ownership) params.set("ownership", opts.ownership);
  for (const t of opts.types ?? []) params.append("type", t);
  for (const s of opts.statuses ?? []) params.append("status", s);
  if (opts.repo_id) params.set("repo_id", opts.repo_id);
  if (opts.play_key) params.set("play_key", opts.play_key);
  if (opts.cursor) params.set("cursor", opts.cursor);
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function listInboxItems(
  workspaceId: string,
  opts: InboxListQuery = {},
  token?: string,
): Promise<InboxListResponse> {
  return apiFetch<InboxListResponse>(
    `/v1/workspaces/${workspaceId}/inbox${buildInboxQuery(opts)}`,
    { token },
  );
}

export function getInboxCounts(
  workspaceId: string,
  token?: string,
): Promise<InboxCountsResponse> {
  return apiFetch<InboxCountsResponse>(
    `/v1/workspaces/${workspaceId}/inbox/counts`,
    { token },
  );
}

export function getInboxItem(
  workspaceId: string,
  itemId: string,
  token?: string,
): Promise<InboxItemDetail> {
  return apiFetch<InboxItemDetail>(
    `/v1/workspaces/${workspaceId}/inbox/${encodeURIComponent(itemId)}`,
    { token },
  );
}

export type InboxDispositionAction =
  | "resolve"
  | "dismiss"
  | "approve"
  | "reject"
  | "answer"
  | "accept"
  | "retry"
  | "acknowledge";

export function applyInboxDisposition(
  workspaceId: string,
  itemId: string,
  body: {
    action: InboxDispositionAction;
    resolution?: string | null;
    answer?: string | null;
    payload?: Record<string, unknown>;
  },
  token?: string,
): Promise<InboxItemDetail> {
  return apiFetch<InboxItemDetail>(
    `/v1/workspaces/${workspaceId}/inbox/${encodeURIComponent(itemId)}/disposition`,
    { method: "POST", body, token },
  );
}

export type InboxDiscussResponse = {
  thread_id: string;
  inbox_item_id: string;
};

/**
 * Open a fresh Navigator chat thread seeded with this inbox item's
 * full context. Used by the "Discuss with Navigator" button on
 * clarifications + blockers — the Navigator gets the source ticket
 * snapshot + the agent's question as a system message so it can help
 * the operator draft an answer immediately.
 */
export function discussInboxItemWithNavigator(
  workspaceId: string,
  itemId: string,
  token?: string,
): Promise<InboxDiscussResponse> {
  return apiFetch<InboxDiscussResponse>(
    `/v1/workspaces/${workspaceId}/inbox/${encodeURIComponent(itemId)}/discuss`,
    { method: "POST", token },
  );
}

export function snoozeInboxItem(
  workspaceId: string,
  itemId: string,
  snoozedUntil: string,
  token?: string,
): Promise<InboxItemDetail> {
  return apiFetch<InboxItemDetail>(
    `/v1/workspaces/${workspaceId}/inbox/${encodeURIComponent(itemId)}/snooze`,
    { method: "POST", body: { snoozed_until: snoozedUntil }, token },
  );
}

export function unsnoozeInboxItem(
  workspaceId: string,
  itemId: string,
  token?: string,
): Promise<InboxItemDetail> {
  return apiFetch<InboxItemDetail>(
    `/v1/workspaces/${workspaceId}/inbox/${encodeURIComponent(itemId)}/unsnooze`,
    { method: "POST", token },
  );
}

export function reassignInboxItem(
  workspaceId: string,
  itemId: string,
  body: { user_id?: string; handle?: string },
  token?: string,
): Promise<InboxItemDetail> {
  return apiFetch<InboxItemDetail>(
    `/v1/workspaces/${workspaceId}/inbox/${encodeURIComponent(itemId)}/reassign`,
    { method: "POST", body, token },
  );
}

export function appendInboxEvent(
  workspaceId: string,
  itemId: string,
  body: { body: string; payload?: Record<string, unknown> },
  token?: string,
): Promise<InboxItemEvent> {
  return apiFetch<InboxItemEvent>(
    `/v1/workspaces/${workspaceId}/inbox/${encodeURIComponent(itemId)}/events`,
    { method: "POST", body, token },
  );
}

// --- Audit log -------------------------------------------------------------

export interface AuditLogFilters {
  limit?: number;
  before?: number | null;
  action?: string | null;
  actor?: string | null;
  target_kind?: string | null;
  since?: string | null;
  until?: string | null;
}

export function listAuditLog(
  workspaceId: string,
  opts: AuditLogFilters = {},
  token?: string,
): Promise<ApiAuditPage> {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.before !== undefined && opts.before !== null)
    params.set("before", String(opts.before));
  if (opts.action) params.set("action", opts.action);
  if (opts.actor) params.set("actor", opts.actor);
  if (opts.target_kind) params.set("target_kind", opts.target_kind);
  if (opts.since) params.set("since", opts.since);
  if (opts.until) params.set("until", opts.until);
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

// ---------------------------------------------------------------------------
// Workspace prose-rule policies (Workspace policy injection)
// ---------------------------------------------------------------------------

export interface ApiPolicy {
  id: string;
  workspace_id: string;
  title: string;
  body: string;
  enabled: boolean;
  sort_order: number;
  /**
   * Role slugs the policy targets. ``null`` means global (renders for
   * every role + Navigator chat). A non-empty list scopes the rule.
   * The backend never returns an empty array — empty is normalised
   * to ``null`` server-side so the Console only branches on
   * "null vs non-empty".
   */
  applies_to_roles: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface ApiPolicyCreateIn {
  title: string;
  body: string;
  enabled?: boolean;
  sort_order?: number;
  /** Omit or pass ``null`` for a global rule. */
  applies_to_roles?: string[] | null;
}

export interface ApiPolicyUpdateIn {
  title?: string;
  body?: string;
  enabled?: boolean;
  sort_order?: number;
  /**
   * Omit the key to leave the scope unchanged. Pass ``null`` to clear
   * back to global, or a list to (re)scope.
   */
  applies_to_roles?: string[] | null;
}

export async function listPolicies(
  workspaceId: string,
  token?: string,
): Promise<ApiPolicy[]> {
  const envelope = await apiFetch<{ policies: ApiPolicy[] }>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/policies`,
    { token },
  );
  return envelope.policies;
}

export async function createPolicy(
  workspaceId: string,
  body: ApiPolicyCreateIn,
  token?: string,
): Promise<ApiPolicy> {
  return apiFetch<ApiPolicy>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/policies`,
    { method: "POST", body, token },
  );
}

export async function updatePolicy(
  workspaceId: string,
  policyId: string,
  body: ApiPolicyUpdateIn,
  token?: string,
): Promise<ApiPolicy> {
  return apiFetch<ApiPolicy>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/policies/${encodeURIComponent(policyId)}`,
    { method: "PATCH", body, token },
  );
}

export async function deletePolicy(
  workspaceId: string,
  policyId: string,
  token?: string,
): Promise<void> {
  await apiFetch<void>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/policies/${encodeURIComponent(policyId)}`,
    { method: "DELETE", token },
  );
}

// ---------------------------------------------------------------------------
// Agent roles — Ship defaults + workspace overrides/clones (Phase 2.4)
// ---------------------------------------------------------------------------

/** One Ship-shipped default specialist (file-backed under
 *  ``backend/app/resources/agent_roles/``). Read-only. */
export interface ApiAgentRoleDefault {
  slug: string;
  name: string;
  fsm_stage: string | null;
}

/** Ship default summary plus the full prompt body. */
export interface ApiAgentRoleDefaultDetail extends ApiAgentRoleDefault {
  prompt: string;
}

/** One workspace agent role row (override or clone). */
export interface ApiAgentRole {
  id: string;
  workspace_id: string;
  slug: string;
  name: string;
  /** ``null`` for an override (slug shadows a Ship default). Set to the
   *  default slug when this row was created as a clone via the modal. */
  base_role_slug: string | null;
  created_at: string;
  updated_at: string;
}

/** Workspace row plus the full prompt body. */
export interface ApiAgentRoleDetail extends ApiAgentRole {
  prompt: string;
}

/** Resolution result from ``GET /agent-roles/{slug}/resolve``. */
export interface ApiAgentRoleResolved {
  slug: string;
  name: string;
  prompt: string;
  fsm_stage: string | null;
  source: "workspace" | "ship_default";
}

export interface ApiAgentRoleCreateIn {
  slug: string;
  name: string;
  prompt: string;
  /** Set when the row is a clone of a Ship default. Drop to create an
   *  override (slug must equal a Ship default slug in that case). */
  base_role_slug?: string | null;
}

export interface ApiAgentRoleUpdateIn {
  name?: string;
  prompt?: string;
}

export function listShipAgentRoleDefaults(
  token?: string,
): Promise<ApiAgentRoleDefault[]> {
  return apiFetch<ApiAgentRoleDefault[]>(`/v1/agent-roles`, { token });
}

export function getShipAgentRoleDefault(
  slug: string,
  token?: string,
): Promise<ApiAgentRoleDefaultDetail> {
  return apiFetch<ApiAgentRoleDefaultDetail>(
    `/v1/agent-roles/${encodeURIComponent(slug)}`,
    { token },
  );
}

export async function listWorkspaceAgentRoles(
  workspaceId: string,
  token?: string,
): Promise<ApiAgentRole[]> {
  const envelope = await apiFetch<{ roles: ApiAgentRole[] }>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-roles`,
    { token },
  );
  return envelope.roles;
}

export function getWorkspaceAgentRole(
  workspaceId: string,
  slug: string,
  token?: string,
): Promise<ApiAgentRoleDetail> {
  return apiFetch<ApiAgentRoleDetail>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-roles/${encodeURIComponent(slug)}`,
    { token },
  );
}

export function createWorkspaceAgentRole(
  workspaceId: string,
  body: ApiAgentRoleCreateIn,
  token?: string,
): Promise<ApiAgentRoleDetail> {
  return apiFetch<ApiAgentRoleDetail>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-roles`,
    { method: "POST", body, token },
  );
}

export function updateWorkspaceAgentRole(
  workspaceId: string,
  slug: string,
  body: ApiAgentRoleUpdateIn,
  token?: string,
): Promise<ApiAgentRoleDetail> {
  return apiFetch<ApiAgentRoleDetail>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-roles/${encodeURIComponent(slug)}`,
    { method: "PUT", body, token },
  );
}

export async function deleteWorkspaceAgentRole(
  workspaceId: string,
  slug: string,
  token?: string,
): Promise<void> {
  await apiFetch<void>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-roles/${encodeURIComponent(slug)}`,
    { method: "DELETE", token },
  );
}

export function resolveWorkspaceAgentRole(
  workspaceId: string,
  slug: string,
  token?: string,
): Promise<ApiAgentRoleResolved> {
  return apiFetch<ApiAgentRoleResolved>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-roles/${encodeURIComponent(slug)}/resolve`,
    { token },
  );
}

// ---------------------------------------------------------------------------
// Repo home (RFC-0008 §F — PR-4 "Now/Trends")
// ---------------------------------------------------------------------------

export type ApiRepoHomeActivityKind = "pipeline" | "workflow" | "agent";
export type ApiRepoHomeActivityStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "other";

export interface ApiRepoHomeRecentActivity {
  kind: ApiRepoHomeActivityKind;
  status: ApiRepoHomeActivityStatus;
  title: string;
  at: string;
  html_url: string | null;
}

export interface ApiRepoHomeNow {
  runs_in_flight: number;
  runs_last_24h: number;
  successes_last_24h: number;
  failures_last_24h: number;
  last_run_at: string | null;
  last_success_at: string | null;
  dispatches_in_flight: number;
  lanes_enabled: number;
  lanes_total: number;
  bundle_installed_version: string | null;
  bundle_current_version: string;
  bundle_drift: boolean;
  install_suspended: boolean;
  install_missing: boolean;
  recent_activity: ApiRepoHomeRecentActivity[];
}

export interface ApiRepoHomeTrendBucket {
  day: string;
  total: number;
  successes: number;
  failures: number;
  other: number;
}

export interface ApiRepoHomeTrendTotals {
  runs: number;
  successes: number;
  failures: number;
  other: number;
  success_rate: number | null;
}

export interface ApiRepoHomeLaneBreakdown {
  lane_id: string;
  runs: number;
  successes: number;
  failures: number;
  last_run_at: string | null;
}

export interface ApiRepoHomeTrends {
  window_days: number;
  buckets: ApiRepoHomeTrendBucket[];
  totals: ApiRepoHomeTrendTotals;
  lanes: ApiRepoHomeLaneBreakdown[];
}

export interface ApiRepoHomeReport {
  workspace_id: string;
  repo_id: string;
  full_name: string;
  generated_at: string;
  window_days: number;
  now: ApiRepoHomeNow;
  trends: ApiRepoHomeTrends;
}

export async function getRepoHome(
  workspaceId: string,
  repoId: string,
  opts: { windowDays?: number; token?: string } = {},
): Promise<ApiRepoHomeReport> {
  const params = new URLSearchParams();
  if (opts.windowDays !== undefined) {
    params.set("window_days", String(opts.windowDays));
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<ApiRepoHomeReport>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/home${suffix}`,
    { token: opts.token },
  );
}

// --- Telegram bot adapter (group ↔ workspace bridge) -----------------------

export interface ApiTelegramBindPreview {
  chat_id: number;
  chat_title: string | null;
  expires_at: string;
  already_bound_workspace_id: string | null;
}

export interface ApiTelegramLink {
  id: string;
  telegram_chat_id: number;
  title: string | null;
  workspace_id: string;
  linked_by_user_id: string;
  has_active_pat: boolean;
  created_at: string;
  updated_at: string;
}

export function previewTelegramBind(
  nonce: string,
  token?: string,
): Promise<ApiTelegramBindPreview> {
  return apiFetch<ApiTelegramBindPreview>(
    `/v1/integrations/telegram/bind/preview?nonce=${encodeURIComponent(nonce)}`,
    { token },
  );
}

export function confirmTelegramBind(
  input: { nonce: string; workspace_id: string },
  token?: string,
): Promise<ApiTelegramLink> {
  return apiFetch<ApiTelegramLink>(
    "/v1/integrations/telegram/bind/confirm",
    { method: "POST", body: input, token },
  );
}

export function listTelegramLinks(
  workspaceId: string,
  token?: string,
): Promise<ApiTelegramLink[]> {
  return apiFetch<ApiTelegramLink[]>(
    `/v1/integrations/telegram/links?workspace_id=${encodeURIComponent(workspaceId)}`,
    { token },
  );
}

export function deleteTelegramLink(
  linkId: string,
  token?: string,
): Promise<void> {
  return apiFetch<void>(
    `/v1/integrations/telegram/links/${encodeURIComponent(linkId)}`,
    { method: "DELETE", token },
  );
}

