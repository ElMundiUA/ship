// Wire types for the Ship `/v1` API.
// Keep in sync with backend/app/api/v1/schemas.py and backend/app/main._build_entry.

/**
 * The seven canonical lifecycle states a stage can sit in.
 *
 *   backlog          ticket exists, no agent picks it up
 *   planning         intake/BA/architects scope work; ticket sits in Linear Todo
 *   executing        dev/QA cycles run; ticket sits in Linear In Progress
 *   reviewing        work submitted, awaiting approval
 *   awaiting_input   frozen, waiting on a human answer (overlay via label)
 *   blocked          frozen, external blocker (overlay via label)
 *   closed           terminal
 *
 * Lives here (not in api/client.ts) so client components can import
 * the constant without tripping the ``server-only`` guard.
 */
export type CanonicalState =
  | "backlog"
  | "planning"
  | "executing"
  | "reviewing"
  | "awaiting_input"
  | "blocked"
  | "closed";

export const CANONICAL_STATES: readonly CanonicalState[] = [
  "backlog",
  "planning",
  "executing",
  "reviewing",
  "awaiting_input",
  "blocked",
  "closed",
];

/** Magic projection value for canonical states that don't move the
 *  ticket between native columns — the adapter only flips a label
 *  while the ticket stays in its current column. */
export const TRACKER_OVERLAY = "__overlay__";

/** Who advances the ticket along a transition. */
export type TransitionActor = "user" | "agent" | "either";

export type ApiUser = {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
};

export type ApiSession = {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  user: ApiUser;
};

export type ApiWorkspace = {
  id: string;
  org_id: string;
  slug: string;
  name: string;
  catalog_sources: Record<string, boolean>;
  /**
   * Workspace-level default for per-state ``default_agent_profile``.
   * NULL means the operator hasn't picked one yet — the /process editor
   * gates all edits until this is set.
   */
  default_agent_profile: string | null;
  created_at: string;
};

export type ApiArtifactKind = "pattern" | "tool" | "collection";

/** Single resolver entry, as produced by `_build_entry` + resolver `_annotate`. */
export type ApiArtifact = {
  id: string;
  title: string | null;
  summary: string;
  description: string;
  path: string;
  tags: string[];
  group: string | null;
  version: string | null;
  content_sha256: string | null;
  updated_at: string | null;
  channel: string | null;
  min_shipctl: string | null;
  deprecated: boolean;
  effective_source: "global" | "workspace" | "project";
  source_repo_id?: string;
};

export type ApiArtifactList = {
  version: 2;
  kind: ApiArtifactKind;
  workspace_id: string;
  catalog_sources: Record<string, boolean>;
} & {
  // The list lands under the plural key (patterns/tools/collections).
  [plural: string]: unknown;
};

/** GET /v1/workspaces/{id}/artifacts/{kind}/{artifact_id} response. */
export type ApiArtifactDetail = ApiArtifact & {
  readme: string;
  layers: ApiArtifact[];
  spec?: Record<string, unknown> | null;
};

export type ApiError = {
  detail: string | { msg: string }[];
};

export type ApiIntegrationKind =
  | "linear"
  | "jira"
  | "confluence"
  | "notion"
  | "github"
  | "gitlab"
  | "slack"
  | "teams"
  | "otel"
  | "webhook"
  | "s3-export";

export type ApiIntegration = {
  id: string;
  workspace_id: string;
  kind: ApiIntegrationKind | string;
  config: Record<string, unknown>;
  status: "pending" | "ok" | "error" | string;
  has_secret: boolean;
  last_health_at: string | null;
  last_health_error: string | null;
  created_at: string;
  updated_at: string;
};

export type ApiArtifactRepo = {
  id: string;
  workspace_id: string;
  kind: "workspace" | "project";
  url: string;
  default_branch: string;
  last_sync_at: string | null;
  last_sync_sha: string | null;
  last_sync_error: string | null;
  created_at: string;
};

export type ApiTokenMint = {
  id: string;
  name: string;
  workspace_id: string | null;
  scopes: string[];
  expires_at: string | null;
  secret: string;
  created_at: string;
};

export type ApiMemberRole =
  | "owner"
  | "admin"
  | "maintainer"
  | "member"
  | "viewer";

export type ApiMember = {
  id: string;
  user_id: string;
  email: string;
  display_name: string | null;
  role: ApiMemberRole;
  /** Which specialist Inbox lanes (BA, QA, …) this person may answer; `["*"]` = all. */
  answer_specialist_slugs: string[];
  pending: boolean;
  created_at: string;
};

export type ApiTokenInfo = {
  id: string;
  name: string;
  workspace_id: string | null;
  prefix: string | null;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
};

export type ApiAuditActor = {
  user_id: string | null;
  user_email: string | null;
  token_id: string | null;
  token_name: string | null;
};

export type ApiAuditEntry = {
  id: number;
  action: string;
  target_kind: string | null;
  target_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  actor: ApiAuditActor;
};

export type ApiAuditPage = {
  items: ApiAuditEntry[];
  next_cursor: number | null;
};

export type ApiKnowledgeBucket = {
  slug: string;
  title: string;
  visibility: "project" | "workspace";
  repo_id: string;
  repo_url: string;
  path: string;
  size: number;
  updated_at: string;
  excerpt: string;
  /** Only present on the detail endpoint. */
  body?: string;
};

// --- Phase 3: scope-resolved bucket ladder ---------------------------------
//
// Backend route: GET /v1/workspaces/{ws}/buckets/resolved
//   ?repo_id=...&project_id=...
// One row per ``(scope × slug × source)`` that's visible from the
// requested context, ordered by priority (workspace ≺ project ≺ repo
// ⊕ user). ``effective=true`` flags the row that wins its slug in the
// caller's current scope (what the UI should highlight). See
// ``backend/docs/knowledge-consolidation.md`` for the scope ladder.

export type ApiBucketScope = "workspace" | "project" | "repo" | "user";

export type ApiBucketSource =
  | "agent_memory"
  | "repo_files"
  | "external_static"
  | "connector_proxy"
  | "audio_transcript"
  | "promoted"
  | "repo_context";

export type ApiKnowledgeSourceKind =
  | "repo_context"
  | "connector"
  | "git_docs"
  | "static_upload"
  | "agent_memory"
  | "repo_files"
  | "audio_transcript"
  | "promoted";

export type ApiKnowledgeImportSourceKind =
  | "notion"
  | "confluence"
  | "static_upload"
  | "docs_repo"
  | "website";

export type ApiKnowledgeSource = {
  id: string;
  bucket_id: string;
  kind: ApiKnowledgeSourceKind | string;
  config: Record<string, unknown>;
  status: "ready" | "syncing" | "error" | "disabled" | string;
  cursor: Record<string, unknown> | null;
  content_fingerprint: string | null;
  last_synced_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

export type ApiKnowledgeImportSource = {
  id: string;
  workspace_id: string;
  integration_id: string | null;
  repo_id: string | null;
  kind: ApiKnowledgeImportSourceKind | string;
  name: string;
  config: Record<string, unknown>;
  status: "ready" | "syncing" | "error" | "disabled" | string;
  sync_cursor: Record<string, unknown> | null;
  content_fingerprint: string | null;
  sync_interval_minutes: number | null;
  last_synced_at: string | null;
  last_error: string | null;
  archived_at: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type ApiKnowledgeIngestionRun = {
  id: string;
  source_id: string;
  status: "pending" | "running" | "done" | "error" | string;
  trigger: string;
  stats: Record<string, unknown>;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

// --- Phase 5d: canonical article shape -------------------------------------

export type ApiBucketArticleStatus =
  | "draft"
  | "published"
  | "superseded"
  | "archived";

export type ApiBucketArticle = {
  id: string;
  bucket_id: string;
  slug: string;
  title: string;
  body_md: string;
  version: number;
  status: ApiBucketArticleStatus;
  provenance: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  /**
   * PR-7A: cross-scope override link. Non-null means this article
   * intentionally overrides a workspace-canonical article — the UI
   * renders a divergence badge and a deep-link to the workspace copy.
   */
  overrides_workspace_article_id?: string | null;
  /** Slug of the workspace bucket that owns the overridden article. */
  overrides_workspace_bucket_slug?: string | null;
};
