"""Reporting window for ops dashboard and repo home Now tab (ELS-7).

Invalid ``window`` query values are rejected at the HTTP layer (FastAPI
``Literal``) with 422 — we do not return empty payloads for bad enums.

``all`` removes time lower-bounds on rolling aggregates while existing
``limit`` / fetch caps still bound result size.

.. note::
   ``24h`` uses ``timedelta(days=1)`` to match the historic ops dashboard
   cutoff (not ``hours=24``), so default behaviour stays byte-for-byte
   aligned with pre-selector callers.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

OpsReportWindow = Literal["24h", "7d", "30d", "all"]

# Cap for ``all`` on repo home: fetch enough history for Now aggregation
# without unbounded scans (see ``repo_home`` fetch range).
REPO_HOME_ALL_FETCH_DAYS = 365


def ops_report_cutoff(
    now: datetime, window: OpsReportWindow
) -> datetime | None:
    """UTC lower bound for time-bounded ops aggregates, or ``None`` for *all*."""
    if window == "all":
        return None
    if window == "24h":
        return now - timedelta(days=1)
    if window == "7d":
        return now - timedelta(days=7)
    return now - timedelta(days=30)


def repo_home_fetch_span_days(window: OpsReportWindow) -> int:
    """Minimum lookback (days) to load activity rows for the selected Now window."""
    if window == "all":
        return REPO_HOME_ALL_FETCH_DAYS
    if window == "24h":
        return 1
    if window == "7d":
        return 7
    return 30


__all__ = [
    "OpsReportWindow",
    "REPO_HOME_ALL_FETCH_DAYS",
    "ops_report_cutoff",
    "repo_home_fetch_span_days",
]
