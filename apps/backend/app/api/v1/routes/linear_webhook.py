"""Linear webhook ingestion (Issue.update → tracker.event.received).

Replaces the 5-min ``tracker_poller`` for workspaces that have wired
their Linear app to fire deliveries at this endpoint. The poller stays
as a 5-min fallback so a webhook miss doesn't strand a workspace.

Provisioning is out of scope here — operators run Linear's
``webhookCreate`` mutation (or wire it via the Settings UI) with our
URL + the shared ``LINEAR_WEBHOOK_SECRET``. The handler maps
``data.team.id`` → ``Integration`` row → workspace, so the backend
needs no per-tenant webhook config.

Wire-shape (Linear webhook v3):

    POST  …/v1/webhooks/linear
    Headers:
      Linear-Signature: <hex hmac-sha256(body, LINEAR_WEBHOOK_SECRET)>
      Linear-Event:     "Issue" | "Comment" | …
      Linear-Delivery:  <uuid>   (idempotency hint, optional)
    Body:
      { "action": "update", "type": "Issue", "data": {...}, ... }
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.tenancy import Integration
from backend.app.db.session import get_session
from backend.app.integrations.linear.webhook import (
    InvalidWebhookSignature,
    verify_signature,
)
from backend.app.services.tracker_poller import _write_transition_event


logger = logging.getLogger(__name__)

router = APIRouter(tags=["linear-webhook"])


def _extract_fsm_stage(labels: list[dict[str, Any]] | None) -> str | None:
    """Pick the ``stage:<id>`` value off an issue's labels list."""
    if not labels:
        return None
    for label in labels:
        name = str(label.get("name") or "")
        if name.startswith("stage:"):
            return name.split(":", 1)[1] or None
    return None


@router.post("/webhooks/linear", status_code=status.HTTP_200_OK)
async def linear_webhook(
    request: Request,
    linear_signature: str | None = Header(default=None, alias="Linear-Signature"),
    linear_event: str | None = Header(default=None, alias="Linear-Event"),
    linear_delivery: str | None = Header(default=None, alias="Linear-Delivery"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Ingest one Linear webhook delivery.

    Day 1 scope: ``Issue.update`` only. That's the event the
    dispatcher cares about — every other Linear event is silently
    200'd so Linear's "Recent Deliveries" stays green and we can
    layer more event types later without redeploying.
    """
    raw = await request.body()
    try:
        verify_signature(raw, linear_signature, settings=settings)
    except InvalidWebhookSignature as exc:
        # 401 (not 400) so a wrong-secret config shows up as an auth
        # issue in Linear's delivery log, not a malformed-payload one.
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="malformed JSON body"
        ) from exc

    event_type = (
        str(linear_event or payload.get("type") or "").strip()
    )
    action = str(payload.get("action") or "").strip().lower()

    # Day-1 contract: only Issue.update updates the FSM. Everything
    # else is logged + 200'd so the next round of work can layer in
    # Comment / Project / Cycle handlers without changing the wire.
    if event_type != "Issue" or action != "update":
        logger.debug(
            "linear webhook ignored: event=%s action=%s delivery=%s",
            event_type, action, linear_delivery,
        )
        return {"ok": True, "event": event_type, "action": action, "applied": False}

    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if not data:
        logger.warning("linear webhook: missing 'data' object")
        return {"ok": True, "event": event_type, "applied": False, "reason": "no_data"}

    ticket_ref = str(data.get("identifier") or "").strip() or None
    new_state = (
        str((data.get("state") or {}).get("name") or "").strip() or None
    )
    team_id = str((data.get("team") or {}).get("id") or "").strip() or None
    updated_at = (
        str(data.get("updatedAt") or "").strip() or None
    )
    fsm_stage = _extract_fsm_stage(data.get("labels"))

    # We need the canonical {ticket_ref, team_id, new_state} triple
    # to feed the dispatcher. Without them this is signal-less noise.
    if not (ticket_ref and team_id and new_state):
        logger.debug(
            "linear webhook: incomplete payload ticket=%s team=%s state=%s",
            ticket_ref, team_id, new_state,
        )
        return {
            "ok": True, "event": event_type, "applied": False,
            "reason": "incomplete_payload",
        }

    # Map Linear team → our workspace via the Integration config row.
    # Cron-side ``_poll_installation`` reads ``config.team_id`` the
    # same way; staying on the same lookup keeps the picker + webhook
    # in lock-step about which workspace owns which Linear team.
    integration = (
        await session.execute(
            select(Integration)
            .where(
                Integration.kind == "linear",
                Integration.config["team_id"].astext == team_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if integration is None:
        logger.info(
            "linear webhook: team_id=%s has no matching integration "
            "(workspace not connected); ticket=%s",
            team_id, ticket_ref,
        )
        return {
            "ok": True, "event": event_type, "applied": False,
            "reason": "no_workspace_for_team",
        }

    workspace_id: uuid.UUID = integration.workspace_id

    # ``old_state`` isn't present in Linear webhook payloads — the
    # event reports the post-update state only. The poll-side path
    # carries the prior state via its cursor; on the webhook path we
    # leave it None so downstream consumers can tell "delivered via
    # webhook, prior state unknown" from "delivered via poller with
    # cursor state X".
    await _write_transition_event(
        session,
        workspace_id=workspace_id,
        ticket_ref=ticket_ref,
        old_state=None,
        new_state=new_state,
        updated_at=updated_at,
        fsm_stage=fsm_stage,
    )
    await session.flush()
    return {
        "ok": True,
        "event": event_type,
        "applied": True,
        "ticket_ref": ticket_ref,
        "workspace_id": str(workspace_id),
    }


__all__ = ["router"]
