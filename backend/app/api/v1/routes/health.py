"""Health endpoint for /v1.

Returns a quick-to-compute liveness check plus a database round-trip so the
probe also covers Postgres reachability.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.schemas import HealthOut
from backend.app.db.session import get_session


router = APIRouter()

VERSION_FILE = Path(__file__).resolve().parents[4].parent / "VERSION"


def _read_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


@router.get("/health", response_model=HealthOut, tags=["meta"])
async def health(session: AsyncSession = Depends(get_session)) -> HealthOut:
    db_status = "ok"
    try:
        result = await session.execute(text("SELECT 1"))
        if result.scalar_one() != 1:
            db_status = "unexpected"
    except Exception as exc:  # pragma: no cover — surfaced as plain text
        db_status = f"error: {type(exc).__name__}: {exc}"
    return HealthOut(status="ok", version=_read_version(), database=db_status)
