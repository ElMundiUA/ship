"""Importing this package registers every ORM model on :data:`Base.metadata`,
so Alembic ``--autogenerate`` and `Base.metadata.create_all` see them all.
"""

from backend.app.db.models.agent_memory import (
    ArtifactFeedback,
    BucketSummary,
    KbChunk,
    KnowledgeBucket,
)
from backend.app.db.models.agent_surface import (
    ChatMessage,
    ChatThread,
    Clarification,
    Improvement,
)
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.notifications import WorkspaceNotification
from backend.app.db.models.pipelines import (
    Pipeline,
    PipelineRun,
    PullRequest,
    WorkflowRun,
)
from backend.app.db.models.repo_secrets import RepoSecret
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
    "ArtifactFeedback",
    "ArtifactRepo",
    "AuditLog",
    "BucketSummary",
    "ChatMessage",
    "ChatThread",
    "Clarification",
    "GitHubInstallation",
    "Improvement",
    "Integration",
    "KbChunk",
    "KnowledgeBucket",
    "Org",
    "OrgMember",
    "Pipeline",
    "PipelineRun",
    "Project",
    "PullRequest",
    "RepoSecret",
    "User",
    "Workspace",
    "WorkspaceInvite",
    "WorkspaceMember",
    "WorkspaceNotification",
    "WorkspaceRepo",
    "WorkflowRun",
]
