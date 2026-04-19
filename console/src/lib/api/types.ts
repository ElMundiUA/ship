// Wire types for the Ship `/v1` API.
// Keep in sync with backend/app/api/v1/schemas.py and backend/app/main._build_entry.

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
  created_at: string;
};

export type ApiArtifactKind = "pattern" | "tool" | "workflow" | "collection";

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
  // The list lands under the plural key (patterns/tools/workflows/collections).
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
