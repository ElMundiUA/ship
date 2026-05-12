"""Importing this package registers every ORM model on :data:`Base.metadata`,
so Alembic ``--autogenerate`` and `Base.metadata.create_all` see them all.
"""

from backend.app.db.models.agent_roles import AgentRole
from backend.app.db.models.dashboard_priorities import WorkspaceProjectPriority
from backend.app.db.models.agent_memory import (
    ArtifactFeedback,
    BucketArticleSource,
    KnowledgeSource,
    KnowledgeImportSource,
    KnowledgeIngestionRun,
    KnowledgeSourceItem,
    BucketSummary,
    KbChunk,
    KnowledgeBucket,
)
from backend.app.db.models.agent_surface import (
    ChatAttachment,
    ChatMessage,
    ChatThread,
    Clarification,
    Improvement,
)
from backend.app.db.models.inbox import (
    GroupAssignmentState,
    InboxItem,
    InboxItemEvent,
    InboxRoutingRule,
    MemberGroup,
    MemberGroupMember,
    RunEscalation,
)
from backend.app.db.models.integrations import (
    GitHubInstallation,
    NativeIntegrationAuditEvent,
    NativeIntegrationBinding,
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationSyncState,
    WorkspaceRepo,
)
from backend.app.db.models.lanes import Routine
from backend.app.db.models.notifications import WorkspaceNotification
from backend.app.db.models.pipelines import (
    AgentRequest,
    FleetRequest,
    Pipeline,
    PipelineRun,
    PullRequest,
    WorkflowRun,
)
from backend.app.db.models.policies import WorkspacePolicy
from backend.app.db.models.repo_intel import RepoIntel, RepoIntelTriggeredBy
from backend.app.db.models.repo_secrets import RepoSecret
from backend.app.db.models.telegram import (
    TelegramChatLink,
    TelegramThreadMap,
)
from backend.app.db.models.tenancy import (
    ApiToken,
    ArtifactRepo,
    AuditLog,
    Integration,
    Org,
    OrgMember,
    PlatformInvite,
    Project,
    User,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
)

__all__ = [
    "AgentRequest",
    "AgentRole",
    "ApiToken",
    "ArtifactFeedback",
    "ArtifactRepo",
    "AuditLog",
    "BucketArticleSource",
    "BucketSummary",
    "ChatAttachment",
    "ChatMessage",
    "ChatThread",
    "Clarification",
    "FleetRequest",
    "GitHubInstallation",
    "GroupAssignmentState",
    "Improvement",
    "InboxItem",
    "InboxItemEvent",
    "InboxRoutingRule",
    "Integration",
    "KbChunk",
    "KnowledgeBucket",
    "KnowledgeImportSource",
    "KnowledgeIngestionRun",
    "KnowledgeSource",
    "KnowledgeSourceItem",
    "MemberGroup",
    "MemberGroupMember",
    "NativeIntegrationAuditEvent",
    "NativeIntegrationBinding",
    "NativeIntegrationCredential",
    "NativeIntegrationInstallation",
    "NativeIntegrationSyncState",
    "Org",
    "OrgMember",
    "Pipeline",
    "PipelineRun",
    "PlatformInvite",
    "Project",
    "PullRequest",
    "RepoIntel",
    "RepoIntelTriggeredBy",
    "Routine",
    "RepoSecret",
    "RunEscalation",
    "TelegramChatLink",
    "TelegramThreadMap",
    "User",
    "Workspace",
    "WorkspaceInvite",
    "WorkspaceMember",
    "WorkspaceNotification",
    "WorkspacePolicy",
    "WorkspaceProjectPriority",
    "WorkspaceRepo",
    "WorkflowRun",
]
