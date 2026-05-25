"""Client for the per-workspace Lighthouse knowledge engine (whafr).

Lighthouse is the multi-tenant flat-RAG retrieval engine that replaces
Ship's internal knowledge index. Reads are scoped per workspace via the
``X-Workspace`` header; retrieval is unauthenticated (network-gated), the
admin importer surface takes an optional bearer token.
"""

from backend.app.integrations.lighthouse.client import (
    LighthouseClient,
    build_lighthouse_client,
)
from backend.app.integrations.lighthouse.provisioning import (
    provision_workspace_knowledge,
)
from backend.app.integrations.lighthouse.s3_writer import (
    KnowledgeS3Writer,
    build_knowledge_s3_writer,
)

__all__ = [
    "KnowledgeS3Writer",
    "LighthouseClient",
    "build_knowledge_s3_writer",
    "build_lighthouse_client",
    "provision_workspace_knowledge",
]
