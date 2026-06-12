"""Provider lifecycle capability checks.

Routes and serializers use these helpers instead of embedding provider-specific
handle rules inline.
"""

from __future__ import annotations

from typing import Any

from backend.app.db.models.deploy import DeploymentStatus as DS


def can_native_rollback(
    *,
    provider: str,
    status: str,
    provider_ref: dict[str, Any] | None,
) -> bool:
    ref = provider_ref or {}
    if provider == "digitalocean":
        return (
            status == DS.ACTIVE
            and bool(ref.get("app_id"))
            and bool(ref.get("deployment_id"))
        )
    return False


__all__ = ["can_native_rollback"]
