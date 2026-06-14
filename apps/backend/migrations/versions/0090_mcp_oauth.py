"""mcp_oauth_clients + mcp_oauth_codes — MCP OAuth broker (ELS-296)

Ship becomes the OAuth 2.1 authorization server for the MCP edge:
clients self-register (DCR), the console issues single-use PKCE-bound
authorization codes under the operator's session, and /oauth/token
exchanges a code for a short-lived workspace-scoped ``ship_pat_`` (an
``api_tokens`` row — no new credential type). Forward revision after
0089 (applied in prod; never edited in place).

Revision ID: 0090_mcp_oauth
Revises: 0089_workflow_runs
Create Date: 2026-06-14
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0090_mcp_oauth"
down_revision: Union[str, None] = "0089_workflow_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("client_id", sa.String(64), primary_key=True),
        sa.Column("client_name", sa.String(200), nullable=True),
        sa.Column(
            "redirect_uris",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "grant_types",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[\"authorization_code\"]'::jsonb"),
        ),
        sa.Column(
            "token_endpoint_auth_method",
            sa.String(40),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "mcp_oauth_codes",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code_hash", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "client_id",
            sa.String(64),
            sa.ForeignKey("mcp_oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column(
            "code_challenge_method",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'S256'"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "scopes",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_mcp_oauth_codes_client_id", "mcp_oauth_codes", ["client_id"]
    )
    op.create_index(
        "ix_mcp_oauth_codes_expires_at", "mcp_oauth_codes", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_oauth_codes_expires_at", table_name="mcp_oauth_codes")
    op.drop_index("ix_mcp_oauth_codes_client_id", table_name="mcp_oauth_codes")
    op.drop_table("mcp_oauth_codes")
    op.drop_table("mcp_oauth_clients")
