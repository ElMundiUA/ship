"""Shared semantics for selectable ops reporting horizons (`window`).

`window` selects a rolling UTC cutoff for time-bounded dashboard slices.
Stale/stuck thresholds (e.g. PR no-activity probes) intentionally stay on
their own constants so diagnostics do not float with this reporting knob.

Invalid `window` query values are rejected by FastAPI (422) via the annotated
Literal type on the route.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

OpsReportingWindow = Literal["24h", "7d", "30d", "all"]

# GitHub webhook path tags Ship-owned workflows with this display-name prefix —
# mirror it here so dashboard failed-run tiles describe Ship CI, not arbitrary
# third-party workflows landed in ``workflow_runs``.
OPS_SHIP_WORKFLOW_NAME_PREFIX = "Ship ·"


def ops_reporting_cutoff(
    now: datetime,
    window: OpsReportingWindow,
) -> datetime | None:
    """UTC lower bound for aggregates that share the ops horizon.

    ``None`` means no time cutoff (subject to SQL ``LIMIT`` caps).
    """
    if window == "all":
        return None
    if window == "24h":
        return now - timedelta(hours=24)
    if window == "7d":
        return now - timedelta(days=7)
    return now - timedelta(days=30)


def repo_activity_fetch_start(
    *,
    now: datetime,
    window: OpsReportingWindow,
    window_days: int,
) -> datetime:
    """Earliest instant we load activity rows for Trends + Now.

    Covers the trends histogram (`window_days` UTC buckets) together with the
    operator-selected ``window`` slice on the Now tab. For ``all``, keep a
    generous but bounded historical reach so we don't scan unbounded rows.
    """
    trend_start = now - timedelta(days=window_days)
    cutoff = ops_reporting_cutoff(now, window)
    if cutoff is None:
        cap = now - timedelta(days=max(window_days, 365))
        # ``cap`` reaches further into the past than ``trend_start`` when the
        # histogram window is narrower than one year — take the furthest anchor.
        return cap if cap < trend_start else trend_start
    return cutoff if cutoff < trend_start else trend_start


__all__ = [
    "OPS_SHIP_WORKFLOW_NAME_PREFIX",
    "OpsReportingWindow",
    "ops_reporting_cutoff",
    "repo_activity_fetch_start",
]
