"""Importing this package registers every ORM model on :data:`Base.metadata`,
so Alembic ``--autogenerate`` and `Base.metadata.create_all` see them all.
"""

from backend.app.db.models.integrations import GitHubInstallation
from backend.app.db.models.tenancy import (
    ApiToken,
    ArtifactRepo,
    AuditLog,
    Integration,
    Org,
    OrgMember,
    Project,
    User,
    Workspace,
    WorkspaceMember,
)

__all__ = [
    "ApiToken",
    "ArtifactRepo",
    "AuditLog",
    "GitHubInstallation",
    "Integration",
    "Org",
    "OrgMember",
    "Project",
    "User",
    "Workspace",
    "WorkspaceMember",
]
