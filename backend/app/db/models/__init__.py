"""Importing this package registers every ORM model on :data:`Base.metadata`,
so Alembic ``--autogenerate`` and `Base.metadata.create_all` see them all.
"""

from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import (
    Pipeline,
    PipelineRun,
    PullRequest,
    WorkflowRun,
)
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
    WorkspaceInvite,
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
    "Pipeline",
    "PipelineRun",
    "Project",
    "PullRequest",
    "User",
    "Workspace",
    "WorkspaceInvite",
    "WorkspaceMember",
    "WorkspaceRepo",
    "WorkflowRun",
]
