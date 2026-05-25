"""Write knowledge documents to S3 for the Lighthouse importer to pull.

Ship emits raw markdown documents to
``s3://<bucket>/<workspace_id>/<source>/<name>.md``; the per-workspace
Lighthouse S3 importer (provisioned at workspace setup) pulls that prefix
and does the chunking/embedding/retrieval.

"S3" is the S3-compatible API — the backing store is **DigitalOcean
Spaces**, reached via ``s3_endpoint_url`` with explicit Spaces keys.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aioboto3

if TYPE_CHECKING:
    from backend.app.core.config import Settings

logger = logging.getLogger(__name__)


class KnowledgeS3Writer:
    """Stateless S3 (DO Spaces) document writer.

    A fresh aioboto3 client per call — these writes are low-frequency
    (one per harvest / agent record), so a per-call client avoids
    event-loop / lifespan ownership questions in the worker + request
    paths.
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region

    async def write_document(
        self,
        *,
        workspace_id,
        source: str,
        name: str,
        markdown: str,
    ) -> str:
        """Put one markdown document; returns the S3 key written.

        Layout ``<workspace_id>/<source>/<name>.md`` keeps every tenant's
        uploads under its own prefix (the importer is scoped to
        ``<workspace_id>/``).
        """
        key = f"{workspace_id}/{source}/{name}.md"
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
        ) as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=markdown.encode("utf-8"),
                ContentType="text/markdown; charset=utf-8",
            )
        return key


def build_knowledge_s3_writer(settings: "Settings") -> KnowledgeS3Writer | None:
    """Construct the writer when S3 (DO Spaces) credentials are configured.

    Returns ``None`` when knowledge-to-S3 isn't wired (local dev, or
    before the K6 cutover) — callers then skip the emit. DO Spaces needs
    explicit keys (no instance IAM), so the presence of both keys is the
    enable signal; ``s3_bucket`` has a non-empty default so it can't gate.
    """
    access = getattr(settings, "s3_access_key", None)
    secret = getattr(settings, "s3_secret_key", None)
    bucket = getattr(settings, "s3_bucket", None)
    if not (access and secret and bucket):
        return None
    return KnowledgeS3Writer(
        bucket=bucket,
        endpoint_url=getattr(settings, "s3_endpoint_url", None),
        access_key=access,
        secret_key=secret,
        region=getattr(settings, "s3_region", None) or "us-east-1",
    )
