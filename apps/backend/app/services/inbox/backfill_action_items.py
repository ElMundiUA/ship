"""ELS-165 — backfill ``action_items`` on legacy clarification rows.

Pre-Phase-2 (ELS-162) the auto-merger and other agents emitted Q1/Q2
options only in the markdown ``comment`` body (``Options: **A** /
**B** / **C**``). Rows already in the inbox carry zero structured
``action_items``, so the new Decision UI shows them as textarea
fallback even though pill controls would render cleanly.

This sweep parses the body of every legacy clarification row and
injects ``payload.action_items[]`` + ``payload.resolution_mode``.
Idempotent: skips rows that already carry action_items.

Trigger:
  - One-shot run on deploy
  - Cron tick every 30 min (``*/30 * * * *``) to catch new agent
    finishes that pre-date the prompt update but post-date the
    deploy of this sweep
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.inbox import InboxItem
from backend.app.db.session import get_sessionmaker

log = logging.getLogger(__name__)

# Match a question heading like ``**Q1.**`` or ``**Q2.**``.
_Q_HEADER_RE = re.compile(r"\*\*Q(\d+)\.\*\*", re.IGNORECASE)
# Match the Options line: ``Options: **A** / **B** / **C**``.
# The slash-separator may have surrounding whitespace.
_OPTIONS_LINE_RE = re.compile(
    r"Options:\s*((?:\*\*[^*]+\*\*\s*/\s*)*\*\*[^*]+\*\*)",
    re.IGNORECASE,
)
_BOLD_TOKEN_RE = re.compile(r"\*\*([^*]+)\*\*")


def _slugify(s: str) -> str:
    """Reduce a Linear-style option label to a stable lowercase slug."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "opt"


def parse_action_items_from_markdown(
    body: str,
) -> tuple[list[dict[str, Any]], str]:
    """Return (action_items, resolution_mode) parsed from a markdown
    clarification body.

    Empty list + ``freeform_only`` when no Q-blocks or Options lines
    are detected. ``single_choice`` when exactly one question, else
    ``multi_select``.
    """
    if not body or not body.strip():
        return [], "freeform_only"

    # Find every ``**Qn.**`` header position; slice the body into one
    # block per question so we don't cross-pollinate options across
    # questions.
    headers = list(_Q_HEADER_RE.finditer(body))
    if not headers:
        return [], "freeform_only"

    blocks: list[tuple[int, str]] = []
    for i, m in enumerate(headers):
        q_num = int(m.group(1))
        start = m.end()
        end = (
            headers[i + 1].start() if i + 1 < len(headers) else len(body)
        )
        blocks.append((q_num, body[start:end]))

    action_items: list[dict[str, Any]] = []
    for q_num, block_text in blocks:
        opts_match = _OPTIONS_LINE_RE.search(block_text)
        if not opts_match:
            continue
        tokens = _BOLD_TOKEN_RE.findall(opts_match.group(1))
        for token in tokens:
            label = token.strip()
            if not label:
                continue
            slug = _slugify(label)
            action_items.append(
                {
                    "id": f"q{q_num}-{slug}"[:64],
                    "kind": "choice",
                    "label": label[:160],
                }
            )

    if not action_items:
        return [], "freeform_only"
    distinct_qs = {it["id"].split("-", 1)[0] for it in action_items}
    mode = "single_choice" if len(distinct_qs) == 1 else "multi_select"
    return action_items, mode


async def backfill_inbox_action_items(session: AsyncSession) -> int:
    """Walk ``new`` clarification rows lacking ``action_items`` and
    inject parsed options. Returns the count of rows updated."""
    stmt = (
        select(InboxItem)
        .where(InboxItem.type == "clarification")
        .where(InboxItem.status == "new")
    )
    rows = (await session.execute(stmt)).scalars().all()
    updated = 0
    for item in rows:
        payload = item.payload or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("action_items"):
            # Already carries structured options — leave alone.
            continue
        body = item.summary or ""
        # Fall back to ``payload.body`` for legacy rows that stashed
        # the markdown there.
        if not body:
            body = payload.get("body") or ""
        action_items, mode = parse_action_items_from_markdown(body)
        if not action_items:
            continue
        new_payload = dict(payload)
        new_payload["action_items"] = action_items
        new_payload["resolution_mode"] = mode
        # SQLAlchemy doesn't detect mutations to JSONB columns when
        # we mutate in-place; reassign to mark dirty.
        item.payload = new_payload
        updated += 1
        log.info(
            "inbox.backfill: item=%s injected %d action_items mode=%s",
            item.id, len(action_items), mode,
        )
    if updated:
        await session.flush()
    return updated


async def backfill_inbox_action_items_tick() -> None:
    """Cron entrypoint."""
    Session = get_sessionmaker()
    async with Session() as session:
        try:
            n = await backfill_inbox_action_items(session)
        except Exception:
            await session.rollback()
            raise
        await session.commit()
    if n:
        log.info("inbox.backfill: backfilled action_items on %d rows", n)


__all__ = [
    "parse_action_items_from_markdown",
    "backfill_inbox_action_items",
    "backfill_inbox_action_items_tick",
]
