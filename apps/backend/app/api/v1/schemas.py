"""Wire-format schemas for the v1 API.

Kept intentionally separate from the SQLAlchemy ORM models so that the on-the-wire
contract can evolve independently from the database schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str | None = None
    avatar_url: str | None = None


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    plan: str
    created_at: datetime


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
    org_id: uuid.UUID | None = None  # optional: defaults to user's personal org


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    slug: str
    name: str
    catalog_sources: dict
    default_agent_profile: str | None = None
    agent_provider: str
    created_at: datetime


class HealthOut(BaseModel):
    status: str
    version: str
    database: str


# --- Auth ---


class LocalSignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)


class LocalLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class CompleteProfileRequest(BaseModel):
    email: EmailStr


class SessionTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


class TokenMintRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    workspace_id: uuid.UUID | None = None
    scopes: list[str] = Field(default_factory=list)
    ttl_days: int | None = Field(default=None, ge=1, le=365 * 2)


class TokenMintOut(BaseModel):
    """Returned exactly once at mint time. ``secret`` is unrecoverable after."""

    id: uuid.UUID
    name: str
    workspace_id: uuid.UUID | None
    scopes: list[str]
    expires_at: datetime | None
    secret: str
    created_at: datetime


class TokenInfoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    workspace_id: uuid.UUID | None
    prefix: str | None
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


# --- Workspace updates ---


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    catalog_sources: dict[str, bool] | None = None
    # Validated against repos._PROCESS_AGENT_PROFILES at the route layer
    # so the schemas module doesn't pull a circular import.
    default_agent_profile: str | None = Field(
        default=None, min_length=1, max_length=64
    )
    # Bound autonomous-pipeline runtime. Validated against
    # :data:`backend.app.services.agent_provider_resolver.SUPPORTED_PROVIDERS`
    # at the route layer.
    agent_provider: str | None = Field(default=None, min_length=1, max_length=16)


class AgentProviderOut(BaseModel):
    """``GET /v1/workspaces/{ws}/agent-provider`` response."""

    workspace_id: uuid.UUID
    kind: str
    supported: list[str]


class AgentProviderUpdate(BaseModel):
    """``PUT /v1/workspaces/{ws}/agent-provider`` body."""

    kind: str = Field(min_length=1, max_length=16)


# --- Members ---


# Workspace-tier roles. Keep in sync with the backend resolver tuples in
# ``backend.app.api.v1.routes.workspaces`` (ROLES_ADMIN/MAINTAIN/READ).
WORKSPACE_ROLES: tuple[str, ...] = ("owner", "admin", "maintainer", "member", "viewer")


class MemberOut(BaseModel):
    """One row in ``GET /v1/workspaces/{id}/members``.

    The ``user_id`` is the same UUID that surfaces in audit log and
    ``WorkspaceMember`` rows. ``pending`` tells the UI whether the row was
    pre-invited (no Auth0 ``external_subject`` yet) so it can render a
    "waiting for first sign-in" badge instead of a fake last-active timestamp.

    ``answer_specialist_slugs`` lists which Inbox / specialist lanes (BA, QA, …)
    the member can take; ``["*"]`` means all lanes.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID  # WorkspaceMember row id; stable for PATCH/DELETE
    user_id: uuid.UUID
    email: EmailStr
    display_name: str | None
    role: str
    pending: bool
    answer_specialist_slugs: list[str]
    created_at: datetime


class MemberInviteRequest(BaseModel):
    """Pre-provision a workspace member by email.

    Auth0 sends the actual invite email out of band (operator copies the
    Auth0 invitation link from the dashboard, or a Management-API integration
    fires the email later). We just create the local rows so that when the
    invitee logs in via Auth0 with the same email, the JIT mapper finds the
    existing row and inherits the chosen workspace role.
    """

    email: EmailStr
    role: str = Field(default="member", pattern=r"^(owner|admin|maintainer|member|viewer)$")
    display_name: str | None = Field(default=None, max_length=200)


class MemberRoleUpdate(BaseModel):
    role: str = Field(pattern=r"^(owner|admin|maintainer|member|viewer)$")


class MemberPatch(BaseModel):
    """Partial update: change role, specialist lanes, or both."""

    role: str | None = Field(
        default=None, pattern=r"^(owner|admin|maintainer|member|viewer)$"
    )
    answer_specialist_slugs: list[str] | None = None

    @field_validator("answer_specialist_slugs")
    @classmethod
    def check_specialist_slugs(
        cls, v: list[str] | None
    ) -> list[str] | None:
        if v is None:
            return v
        allowed = {"ba", "qa", "eng", "sec", "pm", "dev", "*"}
        if "*" in v and len(v) > 1:
            raise ValueError("cannot mix * with other specialist slugs")
        for s in v:
            if s not in allowed:
                raise ValueError(f"invalid specialist slug: {s!r}")
        return v

    @model_validator(mode="after")
    def at_least_one_field(self) -> MemberPatch:
        if self.role is None and self.answer_specialist_slugs is None:
            raise ValueError("at least one of role, answer_specialist_slugs is required")
        return self


class WorkspaceDeleteRequest(BaseModel):
    """Confirmation payload for the destructive workspace delete.

    The console asks the operator to retype the slug; we then verify it
    server-side so a stale tab or a misclick can't nuke the wrong workspace.
    """

    slug_confirmation: str = Field(min_length=1, max_length=64)


# --- Audit log ---


class AuditLogActorOut(BaseModel):
    """Slim user/token reference attached to each audit row.

    Both fields are optional: a system-driven mutation (e.g. cron) records
    no actor, and a token can be deleted (set-null) while the row stays.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID | None = None
    user_email: EmailStr | None = None
    token_id: uuid.UUID | None = None
    token_name: str | None = None


class AuditLogEntryOut(BaseModel):
    """One row in ``GET /v1/workspaces/{id}/audit-log``.

    ``payload`` is the same ``JSONB`` blob the writer recorded. We never
    redact it here — every action that records a value (PAT name, slug,
    email) intentionally chose that shape, and operators rely on it for
    incident review. PAT *secrets* are never written to ``payload`` in the
    first place.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    target_kind: str | None = None
    target_id: str | None = None
    payload: dict
    created_at: datetime
    actor: AuditLogActorOut


class AuditLogPage(BaseModel):
    """Paginated audit-log envelope.

    ``next_cursor`` is the ``id`` to pass back as ``before`` to fetch the
    next (older) page. ``None`` means the caller has reached the bottom of
    the workspace's history.
    """

    items: list[AuditLogEntryOut]
    next_cursor: int | None = None


# --- Artifact repos ---


class ArtifactRepoCreate(BaseModel):
    """Register an artifact source repository for a workspace.

    ``url`` accepts either ``file:///abs/path`` (local source, read inline) or
    a git URL (``https://…``, ``git@…``, ``ssh://…``) which the sync worker
    will clone into a local cache before the resolver picks it up.
    """

    kind: str = Field(pattern=r"^(workspace|project)$")
    url: str = Field(min_length=1, max_length=1024)
    default_branch: str = Field(default="main", max_length=128)


class ArtifactRepoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    url: str
    default_branch: str
    last_sync_at: datetime | None
    last_sync_sha: str | None
    last_sync_error: str | None
    created_at: datetime


# --- Integrations / secrets ---

# Whitelisted integration kinds. Keep in sync with the catalog rendered in
# the console's `/integrations` page; new kinds get added here first so the
# API rejects typos early.
INTEGRATION_KINDS: tuple[str, ...] = (
    "linear",
    "jira",
    "confluence",
    "notion",
    "github",
    "gitlab",
    "slack",
    "teams",
    "otel",
    "webhook",
    "s3-export",
    # Web search + URL fetch. Workspace-scoped API key — the resolver
    # at ``backend.app.services.firecrawl_resolver`` falls back to env
    # ``FIRECRAWL_API_KEY`` when the row is absent, so dev / single-
    # tenant installs work without populating it.
    "firecrawl",
)


class IntegrationUpsert(BaseModel):
    """Create-or-update payload for a workspace integration.

    ``secret`` is write-only — it's encrypted at rest and never returned by
    the API. Pass ``None`` to leave the existing secret untouched (useful when
    editing only the JSON config).
    """

    kind: str = Field(min_length=1, max_length=32)
    config: dict = Field(default_factory=dict)
    secret: str | None = Field(default=None, max_length=8192)


class IntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    config: dict
    status: str
    has_secret: bool
    last_health_at: datetime | None
    last_health_error: str | None
    created_at: datetime
    updated_at: datetime
