"""MCP OAuth broker tables (ELS-296).

Ship acts as the OAuth 2.1 authorization server for the MCP edge,
delegating the human login + consent to the console (which already
owns the Auth0 session + workspace context). Two tables back the flow:

- ``mcp_oauth_clients`` — Dynamic Client Registration (RFC 7591). MCP
  clients (Claude Code / Desktop) self-register their loopback redirect
  URIs and get a public ``client_id`` (no secret; PKCE is the proof).
- ``mcp_oauth_codes`` — single-use authorization codes. Minted by the
  console's consent grant (under the operator's session), bound to the
  PKCE ``code_challenge`` + client + redirect_uri + user + workspace,
  and exchanged once at ``/oauth/token`` for a short-lived
  workspace-scoped Ship PAT (the access token).

The issued access token is a normal ``ship_pat_`` row in
``api_tokens`` — so the existing ``_resolve_pat`` path validates it and
no second credential type is introduced.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701 — shared column helper, intra-package.
    _ts_created,  # noqa: PLC2701
)


class McpOAuthClient(Base):
    """A dynamically-registered MCP OAuth client (RFC 7591).

    ``client_id`` is the opaque public identifier we hand back at
    registration. Public clients only — ``token_endpoint_auth_method``
    is always ``none`` and PKCE (S256) is mandatory, so there is no
    client secret to store.
    """

    __tablename__ = "mcp_oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Exact redirect URIs the client declared at registration. Loopback
    # URIs (http://127.0.0.1 / http://localhost) match port-insensitively
    # at /authorize time per RFC 8252 §7.3; everything else is exact.
    redirect_uris: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    grant_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[\"authorization_code\"]'::jsonb")
    )
    token_endpoint_auth_method: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'none'")
    )
    created_at: Mapped[datetime] = _ts_created()


class McpOAuthCode(Base):
    """A single-use authorization code (OAuth 2.1 code grant + PKCE).

    Minted by the console consent grant under the operator's session,
    redeemed once at ``/oauth/token``. ``consumed_at`` flips on first
    redemption so a replayed code is refused.
    """

    __tablename__ = "mcp_oauth_codes"
    __table_args__ = (
        Index("ix_mcp_oauth_codes_client_id", "client_id"),
        Index("ix_mcp_oauth_codes_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    # SHA-256 of the raw code; the plaintext only ever travels in the
    # redirect back to the client and is never stored.
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    client_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("mcp_oauth_clients.client_id", ondelete="CASCADE"),
        nullable=False,
    )
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'S256'")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _ts_created()
