"""Schema smoke tests for native integration core models."""

from __future__ import annotations

from backend.app.db.base import Base
from backend.app.db.models.integrations import (
    NativeIntegrationAuditEvent,
    NativeIntegrationBinding,
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationSyncState,
)


def test_native_integration_tables_are_registered() -> None:
    expected = {
        "native_integration_installations",
        "native_integration_credentials",
        "native_integration_bindings",
        "native_integration_sync_states",
        "native_integration_audit_events",
    }

    assert expected.issubset(Base.metadata.tables)


def test_native_integration_models_use_expected_table_names() -> None:
    assert (
        NativeIntegrationInstallation.__tablename__
        == "native_integration_installations"
    )
    assert NativeIntegrationCredential.__tablename__ == "native_integration_credentials"
    assert NativeIntegrationBinding.__tablename__ == "native_integration_bindings"
    assert NativeIntegrationSyncState.__tablename__ == "native_integration_sync_states"
    assert NativeIntegrationAuditEvent.__tablename__ == "native_integration_audit_events"
