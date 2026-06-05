"""Fetch a deployment's logs from DigitalOcean for in-console viewing.

DO doesn't return log text directly — its logs endpoint hands back pointers:
* BUILD / DEPLOY → ``historic_urls`` (presigned, time-limited archive URLs), and
* RUN → a ``url`` proxy snapshot of recent runtime output (+ a websocket
  ``live_url`` we don't stream here).

We fetch those server-side (the presigned URLs are short-lived and the Spaces
bucket isn't CORS-open to the browser), strip ANSI colour codes, and tail the
result so a noisy build can't return megabytes. Read-only; never throws on a
missing/expired log (returns empty text instead).
"""

from __future__ import annotations

import logging
import re

import httpx

from backend.app.integrations.digitalocean import client as do


log = logging.getLogger(__name__)

# Order matters only for display; these are DO's three log streams.
LOG_TYPES = ("BUILD", "DEPLOY", "RUN")

# Strip ANSI/VT100 escape sequences (colour, cursor) so the console shows plain
# text rather than ``\x1b[32m`` noise.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Tail cap — a big build log shouldn't blow up the response / the browser.
_MAX_CHARS = 200_000


async def fetch_deploy_logs(
    app_id: str,
    deployment_id: str,
    *,
    log_type: str,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, bool]:
    """Return ``(text, truncated)`` for one log stream of a DO deployment.

    ``truncated`` is True when the log was tailed to the last ``_MAX_CHARS``.
    Best-effort: any fetch failure yields whatever text we did get (possibly "").
    """
    lt = (log_type or "BUILD").upper()
    if lt not in LOG_TYPES:
        lt = "BUILD"

    owns = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True)
    try:
        try:
            info = await do.deployment_logs(
                app_id, deployment_id, log_type=lt, token=token, client=http
            )
        except do.DigitalOceanAPIError as exc:
            log.info("deployment_logs %s/%s type=%s failed: %s", app_id, deployment_id, lt, exc)
            return "", False

        urls = list(info.get("historic_urls") or [])
        if not urls and info.get("url"):
            urls = [info["url"]]

        chunks: list[str] = []
        for url in urls:
            try:
                resp = await http.get(url, timeout=httpx.Timeout(15.0))
                if resp.status_code < 400:
                    chunks.append(resp.text)
            except Exception as exc:  # noqa: BLE001 — log fetch is non-critical
                log.info("log content fetch failed (%s): %s", lt, exc)
    finally:
        if owns:
            await http.aclose()

    text = _ANSI_RE.sub("", "".join(chunks))
    if len(text) > _MAX_CHARS:
        return text[-_MAX_CHARS:], True
    return text, False


__all__ = ["LOG_TYPES", "fetch_deploy_logs"]
