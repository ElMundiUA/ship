"""Best-effort per-workspace Lighthouse provisioning.

Called on workspace setup to create the per-workspace S3 importer on the
Lighthouse engine. Deliberately best-effort: a missing/unreachable
Lighthouse must never fail Ship's workspace creation. It's a no-op when
Lighthouse isn't configured (``LIGHTHOUSE_BASE_URL`` unset), and the
underlying provision call is idempotent, so re-running is safe.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from backend.app.integrations.lighthouse.client import build_lighthouse_client

if TYPE_CHECKING:
    from backend.app.core.config import Settings

logger = logging.getLogger(__name__)


async def provision_workspace_knowledge(
    workspace_id: uuid.UUID, *, settings: "Settings"
) -> bool:
    """Provision the workspace's S3 importer on Lighthouse.

    Returns ``True`` when provisioning was attempted and succeeded,
    ``False`` when Lighthouse is disabled or the call failed (logged).
    Never raises.
    """
    client = build_lighthouse_client(settings)
    if client is None:
        return False
    try:
        await client.provision_s3_importer(workspace_id=workspace_id)
        return True
    except Exception:
        logger.warning(
            "lighthouse provisioning failed for workspace=%s — will retry "
            "on next setup (idempotent)",
            workspace_id,
            exc_info=True,
        )
        return False
