"""Wire-format schemas for the v1 API.

Kept intentionally separate from the SQLAlchemy ORM models so that the on-the-wire
contract can evolve independently from the database schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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
    "notion",
    "github",
    "gitlab",
    "slack",
    "teams",
    "otel",
    "webhook",
    "s3-export",
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
