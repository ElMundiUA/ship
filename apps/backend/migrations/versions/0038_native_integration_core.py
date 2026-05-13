"""native integration core tables

Revision ID: 0038_native_integration_core
Revises: 0037_knowledge_sources
Create Date: 2026-04-25

These tables are the provider-neutral foundation for first-party
integrations such as Azure DevOps PAT, Atlassian Jira/Confluence,
Notion, Linear, and later GitLab. Existing GitHub App tables remain
authoritative for the current GitHub flow.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0038_native_integration_core"
down_revision: Union[str, None] = "0037_knowledge_sources"
branch_labels = None
depends_on = None


_STATUS_CHECK = "status IN ('pending', 'ready', 'error', 'disabled')"


def upgrade() -> None:
    op.create_table(
        "native_integration_installations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("auth_mode", sa.String(length=32), nullable=False),
        sa.Column("external_account_id", sa.String(length=255), nullable=False),
        sa.Column("external_account_name", sa.String(length=255), nullable=True),
        sa.Column("external_account_url", sa.String(length=1024), nullable=True),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_error", sa.Text(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            (
                "provider IN ('github', 'azure_devops', 'atlassian', "
                "'notion', 'linear', 'gitlab')"
            ),
            name="ck_native_integration_installations_provider",
        ),
        sa.CheckConstraint(
            "auth_mode IN ('app', 'oauth', 'pat')",
            name="ck_native_integration_installations_auth_mode",
        ),
        sa.CheckConstraint(
            _STATUS_CHECK,
            name="ck_native_integration_installations_status",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "external_account_id",
            name="uq_native_integration_installations_account",
        ),
    )
    op.create_index(
        "ix_native_integration_installations_workspace_id",
        "native_integration_installations",
        ["workspace_id"],
    )
    op.create_index(
        "ix_native_integration_installations_provider",
        "native_integration_installations",
        ["workspace_id", "provider"],
    )

    op.create_table(
        "native_integration_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("native_integration_installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("secret_fingerprint", sa.String(length=128), nullable=True),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "installation_id",
            "kind",
            name="uq_native_integration_credentials_installation_kind",
        ),
    )
    op.create_index(
        "ix_native_integration_credentials_installation_id",
        "native_integration_credentials",
        ["installation_id"],
    )

    op.create_table(
        "native_integration_bindings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("native_integration_installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column("external_url", sa.String(length=1024), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            _STATUS_CHECK,
            name="ck_native_integration_bindings_status",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "resource_type",
            "external_id",
            name="uq_native_integration_bindings_resource",
        ),
    )
    op.create_index(
        "ix_native_integration_bindings_workspace_id",
        "native_integration_bindings",
        ["workspace_id"],
    )
    op.create_index(
        "ix_native_integration_bindings_installation_id",
        "native_integration_bindings",
        ["installation_id"],
    )

    op.create_table(
        "native_integration_sync_states",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("native_integration_installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("native_integration_bindings.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sync_kind", sa.String(length=64), nullable=False),
        sa.Column("cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            _STATUS_CHECK,
            name="ck_native_integration_sync_states_status",
        ),
    )
    op.create_index(
        "ix_native_integration_sync_states_installation_id",
        "native_integration_sync_states",
        ["installation_id"],
    )
    op.create_index(
        "ix_native_integration_sync_states_binding_id",
        "native_integration_sync_states",
        ["binding_id"],
    )
    op.create_index(
        "uq_native_integration_sync_states_installation_kind",
        "native_integration_sync_states",
        ["installation_id", "sync_kind"],
        unique=True,
        postgresql_where=sa.text("binding_id IS NULL"),
    )
    op.create_index(
        "uq_native_integration_sync_states_binding_kind",
        "native_integration_sync_states",
        ["installation_id", "binding_id", "sync_kind"],
        unique=True,
        postgresql_where=sa.text("binding_id IS NOT NULL"),
    )

    op.create_table(
        "native_integration_audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("native_integration_installations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_kind", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=512), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_native_integration_audit_events_workspace_created",
        "native_integration_audit_events",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_native_integration_audit_events_installation_id",
        "native_integration_audit_events",
        ["installation_id"],
    )
    op.create_index(
        "ix_native_integration_audit_events_actor_user_id",
        "native_integration_audit_events",
        ["actor_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_native_integration_audit_events_actor_user_id",
        table_name="native_integration_audit_events",
    )
    op.drop_index(
        "ix_native_integration_audit_events_installation_id",
        table_name="native_integration_audit_events",
    )
    op.drop_index(
        "ix_native_integration_audit_events_workspace_created",
        table_name="native_integration_audit_events",
    )
    op.drop_table("native_integration_audit_events")

    op.drop_index(
        "uq_native_integration_sync_states_binding_kind",
        table_name="native_integration_sync_states",
    )
    op.drop_index(
        "uq_native_integration_sync_states_installation_kind",
        table_name="native_integration_sync_states",
    )
    op.drop_index(
        "ix_native_integration_sync_states_binding_id",
        table_name="native_integration_sync_states",
    )
    op.drop_index(
        "ix_native_integration_sync_states_installation_id",
        table_name="native_integration_sync_states",
    )
    op.drop_table("native_integration_sync_states")

    op.drop_index(
        "ix_native_integration_bindings_installation_id",
        table_name="native_integration_bindings",
    )
    op.drop_index(
        "ix_native_integration_bindings_workspace_id",
        table_name="native_integration_bindings",
    )
    op.drop_table("native_integration_bindings")

    op.drop_index(
        "ix_native_integration_credentials_installation_id",
        table_name="native_integration_credentials",
    )
    op.drop_table("native_integration_credentials")

    op.drop_index(
        "ix_native_integration_installations_provider",
        table_name="native_integration_installations",
    )
    op.drop_index(
        "ix_native_integration_installations_workspace_id",
        table_name="native_integration_installations",
    )
    op.drop_table("native_integration_installations")
