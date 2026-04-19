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

// --- Onboarding -----------------------------------------------------------

export type ApiRepoProfile = {
  source: string;
  source_kind: string;
  local_path: string;
  cached: boolean;
  suggested_name: string;
  suggested_slug: string;
  head_branch: string | null;
  head_sha: string | null;
  remote_url: string | null;
  file_count: number;
  truncated: boolean;
  languages: Record<string, number>;
  primary_language: string | null;
  frameworks: string[];
  package_managers: string[];
  has_readme: boolean;
  readme_excerpt: string | null;
  has_tests: boolean;
  test_frameworks: string[];
  has_ci: boolean;
  ci_systems: string[];
  code_style_configs: string[];
  recommended_workflows: string[];
};

export type ApiInstallResult = {
  repo_path: string;
  branch: string | null;
  head_before: string | null;
  head_after: string | null;
  commit_made: boolean;
  files: { path: string; bytes_written: number; overwrote_existing: boolean }[];
  installed: {
    id: string;
    name: string | null;
    version: string | null;
    install_target: string;
    contract_path: string;
  }[];
  skipped: { id: string; reason: string }[];
};

export type ApiSeedResult = {
  repo_path: string;
  branch: string | null;
  head_before: string | null;
  head_after: string | null;
  commit_made: boolean;
  docs: {
    slug: string;
    title: string;
    path: string;
    bytes_written: number;
    excerpt: string;
  }[];
};

export function inspectRepo(source: string, token?: string): Promise<ApiRepoProfile> {
  return apiFetch<ApiRepoProfile>("/v1/onboarding/inspect", {
    method: "POST",
    body: { source },
    token,
  });
}

export function scaffoldDemoRepo(
  token?: string,
): Promise<{ path: string; suggestion: string }> {
  return apiFetch<{ path: string; suggestion: string }>(
    "/v1/onboarding/scaffold-demo-repo",
    { method: "POST", token },
  );
}

export function installWorkflows(
  input: { workspace_id: string; repo_source: string; workflow_ids: string[] },
  token?: string,
): Promise<ApiInstallResult> {
  return apiFetch<ApiInstallResult>("/v1/onboarding/install-workflows", {
    method: "POST",
    body: input,
    token,
  });
}

export function seedKnowledge(
  input: {
    workspace_id: string;
    repo_source: string;
    bucket_slugs?: string[];
  },
  token?: string,
): Promise<ApiSeedResult> {
  return apiFetch<ApiSeedResult>("/v1/onboarding/seed-knowledge", {
    method: "POST",
    body: input,
    token,
  });
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
