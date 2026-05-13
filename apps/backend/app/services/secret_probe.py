"""Health-probe an integration secret to flip ``status`` away from ``pending``.

The console renders ``Integration.status`` directly — ``pending`` / ``ok`` /
``error`` — and operators rightly find a row that's stuck on ``pending`` for
weeks both confusing ("did my save go through?") and untrustworthy ("is the
API key still valid?"). This module is the small loop that answers that
question without ever leaking the plaintext secret out of the worker.

Design notes
============

- **Per-kind validators** live in ``PROBERS``. Each takes the decrypted
  secret + the integration's JSON config and returns ``(status, message)``.
  Network probes hit the third-party's cheapest "tell me who I am" endpoint
  (Linear ``viewer``, GitHub ``/user``, Slack ``auth.test``, …); kinds with
  no useful HTTP probe (Jira/Teams without enough config, ``s3-export``,
  generic ``webhook``) fall back to a format-only check so a typo at least
  surfaces as ``error`` instead of ``pending`` forever.
- **Bounded I/O.** All HTTP probes share a ~6s timeout and never raise — a
  flaky third-party can't wedge the worker. ``probe_one`` is the safety
  barrier above that.
- **No secrets ever leave this process.** The worker decrypts in-memory,
  hands the plaintext to a single prober coroutine, and discards the
  reference. Errors that bubble back are sanitised: we only ever record
  status codes and short reason strings.
- **Pure function over the row.** ``probe_one`` returns the new
  ``(status, error_message)`` and lets the caller decide when to write
  back. That keeps the SQLAlchemy session out of the prober and makes
  table-driven tests trivial.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx


log = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 6.0
USER_AGENT = "ship-secret-probe/1"

# (status, message). status is 'ok' | 'error'. message is None when ok.
ProbeResult = tuple[str, str | None]
ProbeFn = Callable[[str, Mapping[str, Any]], Awaitable[ProbeResult]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_check(
    secret: str,
    *,
    min_len: int = 1,
    prefix: str | None = None,
    label: str = "secret",
) -> ProbeResult:
    """Cheap sanity check used as a fallback when no HTTP probe is feasible."""
    if not secret:
        return "error", f"{label} is empty"
    if len(secret) < min_len:
        return "error", f"{label} is shorter than {min_len} characters"
    if prefix is not None and not secret.startswith(prefix):
        return "error", f"{label} should start with '{prefix}'"
    return "ok", None


def _short(message: str, *, limit: int = 480) -> str:
    """Trim long upstream errors so they fit in ``last_health_error``."""
    message = message.strip()
    return message if len(message) <= limit else message[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Per-kind probes
# ---------------------------------------------------------------------------


async def _probe_linear(secret: str, _config: Mapping[str, Any]) -> ProbeResult:
    """Probe a Linear secret end-to-end.

    Runs **two** GraphQL queries in one batch:

    1. ``viewer { id email }`` — basic auth check. A token that fails
       this is straightforwardly broken (revoked / wrong workspace).
    2. ``issues(first: 1) { nodes { id } }`` — exercises the
       ``Read issues`` scope, which is what every downstream Ship
       feature actually needs (``list_tickets``, agent ``runs_query``,
       project anchors, navigator ``list_tickets`` tool).

    Without (2) a Linear OAuth token that lost the ``read`` scope or
    a personal API key whose permissions narrowed mid-rotation can
    silently keep ``status=ok`` while every downstream call returns
    401. We saw this in dogfood on 2026-05-02: the integrations page
    reported ``ok`` while the navigator ``list_tickets`` call
    returned ``Linear API returned 401``. Sending both queries in
    one ``_alias`` batch keeps the probe at one HTTP round-trip.
    """
    if not secret:
        return "error", "secret is empty"
    headers = {
        "Authorization": secret,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            res = await client.post(
                "https://api.linear.app/graphql",
                headers=headers,
                json={
                    "query": (
                        "query ShipLinearProbe { "
                        "viewer { id email } "
                        "issues(first: 1) { nodes { id } } "
                        "}"
                    )
                },
            )
    except httpx.HTTPError as exc:
        return "error", _short(f"network: {exc!s}")

    if res.status_code in (401, 403):
        return "error", f"linear rejected the api key (HTTP {res.status_code})"
    if res.status_code >= 400:
        return "error", f"linear HTTP {res.status_code}"

    try:
        body = res.json()
    except ValueError:
        return "error", "linear returned a non-JSON response"
    errors = body.get("errors") if isinstance(body, dict) else None
    if errors:
        first = errors[0] if isinstance(errors, list) and errors else errors
        # Most informative bit Linear returns on a partial-scope token
        # is the message + path. Surface them so the operator sees
        # "missing read access on issues" rather than just "graphql
        # error" — they need to know which scope to re-authorise.
        msg = ""
        if isinstance(first, dict):
            msg = str(first.get("message") or first)
            path = first.get("path")
            if path:
                msg = f"{msg} (path={path})"
        else:
            msg = str(first)
        return "error", _short(f"linear graphql error: {msg}")
    data = (body or {}).get("data") or {}
    viewer = data.get("viewer")
    if not isinstance(viewer, dict) or not viewer.get("id"):
        return "error", "linear viewer query returned no id"
    issues = data.get("issues")
    if not isinstance(issues, dict) or "nodes" not in issues:
        # Scope-narrowed tokens often resolve to ``data.issues = null``
        # without a top-level ``errors`` block. Treat that the same as
        # a partial-permission failure so downstream ``list_tickets``
        # 401s become discoverable on the integrations page.
        return "error", "linear issues query returned no payload (missing read scope?)"
    return "ok", None


async def _probe_github(secret: str, _config: Mapping[str, Any]) -> ProbeResult:
    if not secret:
        return "error", "secret is empty"
    headers = {
        # GitHub accepts both 'token <pat>' and 'Bearer <pat>' for classic
        # PATs and fine-grained tokens. 'Bearer' covers GitHub Apps too.
        "Authorization": f"Bearer {secret}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            res = await client.get("https://api.github.com/user", headers=headers)
    except httpx.HTTPError as exc:
        return "error", _short(f"network: {exc!s}")
    if res.status_code in (401, 403):
        return "error", f"github rejected the token (HTTP {res.status_code})"
    if res.status_code >= 400:
        return "error", f"github HTTP {res.status_code}"
    return "ok", None


async def _probe_gitlab(secret: str, config: Mapping[str, Any]) -> ProbeResult:
    if not secret:
        return "error", "secret is empty"
    host = str(config.get("host") or "gitlab.com").strip().rstrip("/")
    base = host if host.startswith("http") else f"https://{host}"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            res = await client.get(
                f"{base}/api/v4/user",
                headers={"PRIVATE-TOKEN": secret, "User-Agent": USER_AGENT},
            )
    except httpx.HTTPError as exc:
        return "error", _short(f"network: {exc!s}")
    if res.status_code in (401, 403):
        return "error", f"gitlab rejected the token (HTTP {res.status_code})"
    if res.status_code >= 400:
        return "error", f"gitlab HTTP {res.status_code}"
    return "ok", None


async def _probe_slack(secret: str, _config: Mapping[str, Any]) -> ProbeResult:
    if not secret:
        return "error", "secret is empty"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            res = await client.post(
                "https://slack.com/api/auth.test",
                headers={
                    "Authorization": f"Bearer {secret}",
                    "User-Agent": USER_AGENT,
                },
            )
    except httpx.HTTPError as exc:
        return "error", _short(f"network: {exc!s}")
    if res.status_code >= 500:
        return "error", f"slack HTTP {res.status_code}"
    try:
        body = res.json()
    except ValueError:
        return "error", "slack returned a non-JSON response"
    # auth.test always returns 200 — the real verdict is the `ok` field.
    if not isinstance(body, dict) or not body.get("ok"):
        reason = (body or {}).get("error") or "unknown"
        return "error", f"slack auth.test rejected the token: {reason}"
    return "ok", None


async def _probe_jira(secret: str, config: Mapping[str, Any]) -> ProbeResult:
    """Jira needs username + host + token to probe; lacking config we fall back."""
    host = str(config.get("host") or "").strip().rstrip("/")
    user = str(config.get("user") or config.get("email") or "").strip()
    if not host or not user:
        return _format_check(secret, min_len=12, label="jira api token")
    base = host if host.startswith("http") else f"https://{host}"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            res = await client.get(
                f"{base}/rest/api/3/myself",
                auth=(user, secret),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        return "error", _short(f"network: {exc!s}")
    if res.status_code in (401, 403):
        return "error", f"jira rejected the token (HTTP {res.status_code})"
    if res.status_code >= 400:
        return "error", f"jira HTTP {res.status_code}"
    return "ok", None


async def _probe_notion(secret: str, _config: Mapping[str, Any]) -> ProbeResult:
    """Probe a Notion integration token end-to-end.

    Two requests in sequence:

    1. ``GET /v1/users/me`` — basic auth check. 401 here means the
       token is broken; nothing else will work.
    2. ``POST /v1/search`` with ``page_size: 1`` — exercises the
       integration's **Read content** capability. Notion gates content
       reads behind a per-integration capability flag (separate from
       the token itself), and a token whose integration was created
       without that capability passes ``users.me`` while every
       downstream tracker call (database query, page read) returns
       403. Mirrors the Linear ``Read issues`` probe extension —
       the same partial-scope failure mode the dogfood transcript on
       2026-05-02 caught for Linear can land identically on Notion if
       an operator picks "user-only" capabilities.

    Same one-round-trip-each shape as the Linear probe; we don't fold
    into a single batch because Notion's API doesn't compose like
    GraphQL does.
    """
    if not secret:
        return "error", "secret is empty"
    headers = {
        "Authorization": f"Bearer {secret}",
        "Notion-Version": "2022-06-28",
        "User-Agent": USER_AGENT,
    }
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            res = await client.get(
                "https://api.notion.com/v1/users/me", headers=headers
            )
    except httpx.HTTPError as exc:
        return "error", _short(f"network: {exc!s}")
    if res.status_code in (401, 403):
        return "error", f"notion rejected the token (HTTP {res.status_code})"
    if res.status_code >= 400:
        return "error", f"notion HTTP {res.status_code}"
    try:
        body = res.json()
    except ValueError:
        return "error", "notion returned a non-JSON response"
    if not isinstance(body, dict) or body.get("object") != "user":
        return "error", "notion users.me did not return a user object"

    # Read-content capability check. ``page_size=1`` keeps the
    # response trivial; we don't care WHAT the workspace has, only
    # that the integration is allowed to read.
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            search_res = await client.post(
                "https://api.notion.com/v1/search",
                headers={**headers, "Content-Type": "application/json"},
                json={"page_size": 1},
            )
    except httpx.HTTPError as exc:
        return "error", _short(f"network: {exc!s}")
    if search_res.status_code in (401, 403):
        return "error", (
            "notion rejected search "
            f"(HTTP {search_res.status_code}) — integration likely "
            "lacks the Read content capability"
        )
    if search_res.status_code >= 400:
        return "error", f"notion search HTTP {search_res.status_code}"
    try:
        search_body = search_res.json()
    except ValueError:
        return "error", "notion search returned a non-JSON response"
    if not isinstance(search_body, dict) or "results" not in search_body:
        return "error", (
            "notion search returned no results array — likely "
            "missing Read content capability"
        )
    return "ok", None


async def _probe_webhook(secret: str, config: Mapping[str, Any]) -> ProbeResult:
    """Generic webhook: format-check the secret + sanity-check the URL."""
    url = str(config.get("url") or "").strip()
    if not url:
        return "error", "webhook URL is missing from config"
    if not (url.startswith("https://") or url.startswith("http://")):
        return "error", "webhook URL must be http(s)://"
    return _format_check(secret, min_len=8, label="hmac signing secret")


async def _probe_teams(secret: str, _config: Mapping[str, Any]) -> ProbeResult:
    if not secret.startswith("https://"):
        return "error", "teams webhook should be the full https:// URL"
    return "ok", None


async def _probe_otel(secret: str, config: Mapping[str, Any]) -> ProbeResult:
    endpoint = str(config.get("endpoint") or "").strip()
    if not endpoint:
        return "error", "otel endpoint is missing from config"
    if not (endpoint.startswith("https://") or endpoint.startswith("http://")):
        return "error", "otel endpoint must be http(s)://"
    return _format_check(secret, min_len=12, label="otel bearer token")


async def _probe_s3_export(secret: str, config: Mapping[str, Any]) -> ProbeResult:
    bucket = str(config.get("bucket") or "").strip()
    if not bucket:
        return "error", "s3 bucket is missing from config"
    return _format_check(secret, min_len=16, label="s3 secret key")


PROBERS: dict[str, ProbeFn] = {
    "linear": _probe_linear,
    "github": _probe_github,
    "gitlab": _probe_gitlab,
    "slack": _probe_slack,
    "jira": _probe_jira,
    "notion": _probe_notion,
    "teams": _probe_teams,
    "otel": _probe_otel,
    "webhook": _probe_webhook,
    "s3-export": _probe_s3_export,
}


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


async def probe_one(
    kind: str, secret: str, config: Mapping[str, Any] | None = None
) -> ProbeResult:
    """Run the prober for ``kind`` and absorb any unexpected exception.

    Unknown kinds resolve to a generic format check so that adding a new
    integration kind never silently leaves rows ``pending`` — the worst
    case is a benign ``ok`` based on length/presence alone.
    """
    config = config or {}
    fn = PROBERS.get(kind)
    if fn is None:
        log.info("secret_probe: no kind-specific prober for %s; using format check", kind)
        return _format_check(secret, label=f"{kind} secret")
    try:
        return await fn(secret, config)
    except Exception as exc:  # noqa: BLE001 — last-line safety net
        log.exception("secret_probe: unexpected error in %s prober", kind)
        return "error", _short(f"probe crashed: {exc!s}")


__all__ = [
    "PROBE_TIMEOUT_SECONDS",
    "PROBERS",
    "ProbeFn",
    "ProbeResult",
    "probe_one",
]
