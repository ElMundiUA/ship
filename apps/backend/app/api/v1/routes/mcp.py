"""Ship MCP Edge (thesis 9, ELS-268/269/272/273/274/275).

The engine exposed as an MCP server: the operator attaches Ship to
the agent they already live in (Claude Code / Desktop / anything
speaking MCP) and that agent becomes the brain — Ship provides the
domain tools and keeps the control plane. Generalizes thesis 4
("chat = edge") to "any operator agent = edge"; the inversion trap
stays closed because this authenticates the OPERATOR's personal PAT,
never a spawned execution agent.

Transport: minimal streamable-HTTP — a single ``POST /mcp`` JSON-RPC
endpoint (stateless mode; the MCP spec allows servers that neither
issue session ids nor open SSE streams, and Claude clients handle
both). Hand-rolled on purpose: the protocol subset we need
(initialize / tools/list / tools/call / ping) is ~100 lines, which is
cheaper than carrying the SDK dependency in the prod image.

ONE tool registry: tools are projected from ``ToolBox.specs()``
through :data:`MCP_TOOL_ALLOWLIST` and dispatched into the existing
handlers — never a second catalog (W8-style single-source rule). The
adapter injects an optional ``workspace_id`` parameter into every
projected tool (PATs can be members of several workspaces) and adds a
handful of MCP-native tools (workspace listing/creation, sibling
GitHub-install attach) that have no chat-tool equivalent.

Stakes policy (ELS-273): enforced HERE, server-side — an external
agent's prompt etiquette is advisory by definition. Approval-type
inbox items demand a verbatim ``approval_echo`` (anti silent-auto-
approve), and hard-destructive actions are not exposed at all: they
remain web-only behind the Console's typed-slug confirmation.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.tenancy import Workspace, WorkspaceMember
from backend.app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSION = "2025-03-26"

# ---------------------------------------------------------------------------
# Policy (ELS-269 allowlist + ELS-273 stakes)
# ---------------------------------------------------------------------------

# ToolBox tools projected into MCP. Deliberately NOT everything:
# - web_fetch is excluded — the operator's agent has its own web
#   tools and proxying fetches through Ship is an SSRF surface with
#   zero upside;
# - recall / recall_context are excluded — Navigator-thread memory
#   has no meaning for an external-agent session.
MCP_TOOL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "audit_search",
        "config_help",
        "config_put",
        "dashboard_get",
        "inbox_create",
        "inbox_get",
        "inbox_list",
        "inbox_update",
        "knowledge_bucket_get",
        "knowledge_search",
        "members_list",
        "pr_get",
        "pr_list",
        "project_create",
        "project_get",
        "project_list",
        "project_update",
        "propose_mass_plan",
        "repo_file_get",
        "repo_symbols",
        "repo_tree",
        "run_subagent",
        "run_workflow",
        "runs_get",
        "runs_list",
        "ticket_create",
        "ticket_get",
        "ticket_list",
        "ticket_update",
    }
)

# Inbox item types whose ``dispose`` is an APPROVAL: committing one
# from an external agent requires the verbatim echo contract below.
APPROVAL_ITEM_TYPES: frozenset[str] = frozenset({"approval"})

# Inbox items the MCP edge refuses to dispose at all (web-only,
# Console typed-slug surface). Matched against ``payload.stakes``.
WEB_ONLY_STAKES: frozenset[str] = frozenset({"destructive", "web_only"})


SERVER_INSTRUCTIONS = """\
You are connected to Ship — the delivery engine that plans, builds,
reviews and ships software through the operator's tracker (Linear/…)
and GitHub. You act under the OPERATOR'S personal access token: every
mutation is attributed to them, so never fire a mutating tool the
operator did not explicitly ask for in this conversation
(verify-before-mutate). Suggest first; call after they confirm.

Workspaces: every tool accepts an optional `workspace_id`. If the
operator belongs to exactly one workspace it is inferred; otherwise
call `list_workspaces` and ask which one they mean.

Typical flows:
- "create a feature / project" → `project_create` (brief as body),
  then `run_subagent` with kind="decomposition" to have Ship's
  planner break it into tickets.
- "what's in flight" → `dashboard_get`, `ticket_list`, `runs_list`.
- "do ticket X" → `ticket_update` with the target state — the status
  field is the engine's only transition signal; the FSM takes over
  from there.
- "review this PR" → `run_workflow` with workflow_name="pr-review".
- open questions / approvals → `inbox_list`, then `inbox_get` and
  `inbox_update`. Disposing an APPROVAL item requires passing
  `approval_echo` with the item's exact title — quote it to the
  operator and get a yes before calling. Items Ship marks as
  destructive cannot be approved here at all; send the operator the
  `web_url` from the error instead.

Hard rule: you cannot spawn or steer Ship's execution agents
directly — you create work items and the control plane (leases,
caps, cascade limits) runs them.
"""


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------


async def _member_workspaces(
    session: AsyncSession, user_id: uuid.UUID
) -> list[Workspace]:
    rows = (
        await session.execute(
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id)
            .order_by(Workspace.created_at.asc())
        )
    ).scalars().all()
    return list(rows)


class _McpToolError(Exception):
    """Tool-level failure — surfaced as an MCP tool result with
    ``isError: true`` (protocol errors use JSON-RPC error objects)."""


async def _resolve_workspace_id(
    session: AsyncSession, auth: AuthContext, arguments: dict[str, Any]
) -> uuid.UUID:
    raw = arguments.pop("workspace_id", None)
    workspaces = await _member_workspaces(session, auth.user.id)
    if raw:
        try:
            ws_id = uuid.UUID(str(raw))
        except ValueError as exc:
            raise _McpToolError(f"invalid workspace_id: {raw!r}") from exc
        if ws_id not in {w.id for w in workspaces}:
            raise _McpToolError(
                "you are not a member of that workspace; call "
                "list_workspaces for the available set"
            )
        return ws_id
    if len(workspaces) == 1:
        return workspaces[0].id
    listing = ", ".join(f"{w.slug}={w.id}" for w in workspaces) or "(none)"
    raise _McpToolError(
        "workspace_id is required (the token belongs to several "
        f"workspaces): {listing}"
    )


# ---------------------------------------------------------------------------
# Tool registry (single source: ToolBox.specs + a few natives)
# ---------------------------------------------------------------------------

_WORKSPACE_ID_PROP = {
    "type": "string",
    "description": (
        "Workspace UUID. Optional when the operator belongs to "
        "exactly one workspace."
    ),
}

_NATIVE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_workspaces",
        "description": (
            "List workspaces the operator's token can act in "
            "(id, slug, name)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "workspace_create",
        "description": (
            "Create a new Ship workspace owned by the operator. "
            "Second-and-later workspaces in an org can attach the "
            "existing GitHub App install headlessly via "
            "github_sibling_installs + github_attach_install; the "
            "first-ever workspace needs the one-time OAuth dance in "
            "the browser (the result carries the wiring URL)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "slug": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "URL-safe slug, unique within the org.",
                },
            },
            "required": ["name", "slug"],
        },
    },
    {
        "name": "github_sibling_installs",
        "description": (
            "List GitHub App installations from sibling workspaces the "
            "operator admins — candidates for headless attach to the "
            "given workspace."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"workspace_id": _WORKSPACE_ID_PROP},
        },
    },
    {
        "name": "github_attach_install",
        "description": (
            "Attach an existing GitHub App installation (from a sibling "
            "workspace) to the given workspace — the headless half of "
            "workspace wiring."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": _WORKSPACE_ID_PROP,
                "installation_id": {
                    "type": "integer",
                    "description": "GitHub's numeric installation id.",
                },
            },
            "required": ["installation_id"],
        },
    },
]


def _projected_tools(settings: Settings) -> list[dict[str, Any]]:
    """ToolBox.specs() → MCP tool definitions (allowlist + injected
    workspace_id). The ToolBox instance here is schema-only — no
    session, never invoked."""
    from backend.app.services.agent.tools import ToolBox

    schema_box = ToolBox(
        None,  # type: ignore[arg-type] — specs() never touches the session
        settings=settings,
        workspace_id=uuid.uuid4(),
        user_id=None,
    )
    tools: list[dict[str, Any]] = []
    for spec in schema_box.specs():
        if spec.name not in MCP_TOOL_ALLOWLIST:
            continue
        params = dict(spec.parameters or {"type": "object", "properties": {}})
        props = dict(params.get("properties") or {})
        props["workspace_id"] = _WORKSPACE_ID_PROP
        if spec.name == "inbox_update":
            props["approval_echo"] = {
                "type": "string",
                "description": (
                    "REQUIRED when disposing an approval-type item: the "
                    "item's exact title, echoed verbatim. Quote it to "
                    "the operator and get an explicit yes first."
                ),
            }
        params["properties"] = props
        tools.append(
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": params,
            }
        )
    tools.extend(_NATIVE_TOOLS)
    return tools


# ---------------------------------------------------------------------------
# Stakes gate (ELS-273) — runs BEFORE dispatch
# ---------------------------------------------------------------------------


async def _stakes_gate(
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
    name: str,
    arguments: dict[str, Any],
) -> None:
    if name != "inbox_update":
        return
    if arguments.get("action") != "dispose":
        return
    raw_id = arguments.get("item_id") or arguments.get("id")
    if not raw_id:
        return  # the handler will reject the malformed call itself
    try:
        item = await session.get(InboxItem, uuid.UUID(str(raw_id)))
    except ValueError:
        return
    if item is None or item.workspace_id != workspace_id:
        return
    stakes = str((item.payload or {}).get("stakes") or "").lower()
    if stakes in WEB_ONLY_STAKES:
        web_url = (
            f"{settings.console_url.rstrip('/')}/inbox"
            f"?ws={workspace_id}&selected={item.id}"
        )
        raise _McpToolError(
            "this item is marked destructive and cannot be approved "
            f"over MCP — the operator must act in the Console: {web_url}"
        )
    if item.type in APPROVAL_ITEM_TYPES:
        echo = str(arguments.pop("approval_echo", "") or "").strip()
        if echo != (item.title or "").strip():
            raise _McpToolError(
                "approval items require approval_echo matching the "
                f"item title verbatim. Title: {item.title!r}. Quote it "
                "to the operator and retry only after an explicit yes."
            )
    arguments.pop("approval_echo", None)


# ---------------------------------------------------------------------------
# Native tool handlers
# ---------------------------------------------------------------------------


async def _call_native(
    name: str,
    arguments: dict[str, Any],
    *,
    session: AsyncSession,
    auth: AuthContext,
    settings: Settings,
) -> str:
    import json

    if name == "list_workspaces":
        rows = await _member_workspaces(session, auth.user.id)
        return json.dumps(
            [{"id": str(w.id), "slug": w.slug, "name": w.name} for w in rows]
        )

    if name == "workspace_create":
        from backend.app.api.v1.routes.workspaces import create_workspace
        from backend.app.api.v1.schemas import WorkspaceCreate

        payload = WorkspaceCreate(
            name=str(arguments.get("name") or "").strip(),
            slug=str(arguments.get("slug") or "").strip(),
        )
        out = await create_workspace(payload, auth=auth, session=session)
        wiring = (
            f"{settings.console_url.rstrip('/')}/onboarding"
            f"?step=github&ws={out.id}"
        )
        return json.dumps(
            {
                "id": str(out.id),
                "slug": out.slug,
                "name": out.name,
                "next": (
                    "attach GitHub via github_sibling_installs + "
                    "github_attach_install (same org), or the one-time "
                    f"browser wiring: {wiring}"
                ),
            }
        )

    if name == "github_sibling_installs":
        from backend.app.api.v1.routes.github_app import (
            _list_linkable_install_candidates,
        )

        ws_id = await _resolve_workspace_id(session, auth, arguments)
        candidates = await _list_linkable_install_candidates(
            session, workspace_id=ws_id, user_id=auth.user.id
        )
        return json.dumps(
            [
                {
                    "installation_id": c.installation_id,
                    "account_login": c.account_login,
                    "from_workspace": c.source_workspace_slug,
                }
                for c in candidates
            ]
        )

    if name == "github_attach_install":
        from backend.app.api.v1.routes.github_app import (
            _link_installation_from_sibling,
        )

        ws_id = await _resolve_workspace_id(session, auth, arguments)
        installation_id = arguments.get("installation_id")
        if not isinstance(installation_id, int):
            raise _McpToolError("installation_id (integer) is required")
        row = await _link_installation_from_sibling(
            session,
            workspace_id=ws_id,
            user_id=auth.user.id,
            installation_id=installation_id,
        )
        return json.dumps(
            {
                "attached": True,
                "installation_id": row.installation_id,
                "account_login": row.account_login,
            }
        )

    raise _McpToolError(f"unknown tool: {name}")


_NATIVE_NAMES = {t["name"] for t in _NATIVE_TOOLS}


async def _call_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    session: AsyncSession,
    auth: AuthContext,
    settings: Settings,
) -> str:
    if name in _NATIVE_NAMES:
        return await _call_native(
            name, arguments, session=session, auth=auth, settings=settings
        )
    if name not in MCP_TOOL_ALLOWLIST:
        raise _McpToolError(f"unknown tool: {name}")

    from backend.app.services.agent.tools import ToolBox

    ws_id = await _resolve_workspace_id(session, auth, arguments)
    await _stakes_gate(session, settings, ws_id, name, arguments)
    toolbox = ToolBox(
        session,
        settings=settings,
        workspace_id=ws_id,
        user_id=auth.user.id,
    )
    return await toolbox.invoke(name, arguments)


# ---------------------------------------------------------------------------
# JSON-RPC endpoint (stateless streamable HTTP)
# ---------------------------------------------------------------------------


def _rpc_result(req_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }
    )


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _rpc_error(None, -32700, "parse error")
    if not isinstance(body, dict):
        return _rpc_error(None, -32600, "batch requests are not supported")

    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params") or {}

    # Notifications (no id) — acknowledge and move on.
    if req_id is None:
        return Response(status_code=202)

    if method == "initialize":
        client_version = str(params.get("protocolVersion") or PROTOCOL_VERSION)
        return _rpc_result(
            req_id,
            {
                "protocolVersion": client_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ship", "version": "1.0.0"},
                "instructions": SERVER_INSTRUCTIONS,
            },
        )

    if method == "ping":
        return _rpc_result(req_id, {})

    if method == "tools/list":
        return _rpc_result(req_id, {"tools": _projected_tools(settings)})

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = dict(arguments) if isinstance(arguments, dict) else {}
        try:
            text = await _call_tool(
                name, arguments, session=session, auth=auth, settings=settings
            )
            await session.commit()
            return _rpc_result(
                req_id,
                {"content": [{"type": "text", "text": text}], "isError": False},
            )
        except _McpToolError as exc:
            await session.rollback()
            return _rpc_result(
                req_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        except Exception as exc:  # noqa: BLE001 — tool failures are results, not 500s
            await session.rollback()
            logger.warning("mcp tool %s failed: %s", name, exc)
            return _rpc_result(
                req_id,
                {
                    "content": [
                        {"type": "text", "text": f"tool failed: {exc}"}
                    ],
                    "isError": True,
                },
            )

    return _rpc_error(req_id, -32601, f"method not found: {method}")


@router.get("/mcp")
async def mcp_get() -> Response:
    # Stateless mode: no server-initiated SSE stream. 405 tells the
    # client to keep using plain POST per the streamable HTTP spec.
    return Response(status_code=405)
