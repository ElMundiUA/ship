"""Sentry initialisation for ``ship-server`` and ``ship-worker`` (RFC-0006 phase 2).

Two design choices deliberately enforced here:

1. **Single entry point** — both the API process (``backend.app.main``) and
   the arq worker (``backend.app.workers.main``) call :func:`init_sentry`
   exactly once on boot. The module is import-safe and idempotent, so the
   "is Sentry already up?" check lives in one place.
2. **Empty DSN means off** — laptops, CI, the test database fixture, and
   the README "first run" all leave ``SENTRY_DSN`` unset. We must never
   raise during init in that case; the call is just a no-op and the rest
   of the app keeps booting.

Sensitive data: the FastAPI integration sends request URLs and headers by
default. We strip ``Authorization`` and the workspace-scoped session cookie
in :func:`_before_send` so PATs and Auth0 access tokens never leave the host.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.app.core.config import Settings, get_settings


log = logging.getLogger("ship.sentry")

_SENTRY_INITIALISED = False

# Headers stripped from event payloads before the SDK ships them. Lower-cased;
# we match case-insensitively because both werkzeug-style and httpx-style
# captures may be present.
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-ship-token",
    }
)


def _scrub_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    if not headers:
        return headers
    cleaned: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _SENSITIVE_HEADERS:
            cleaned[key] = "[scrubbed]"
        else:
            cleaned[key] = value
    return cleaned


def _before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Strip bearer tokens / cookies before the SDK ships an event."""
    request = event.get("request")
    if isinstance(request, dict):
        request["headers"] = _scrub_headers(request.get("headers"))
        # Drop raw cookies even when the integration captured them as a
        # separate field (newer sentry-sdk versions).
        if "cookies" in request:
            request["cookies"] = "[scrubbed]"
    return event


def init_sentry(*, service_name: str | None = None, settings: Settings | None = None) -> bool:
    """Initialise the Sentry SDK if a DSN is configured.

    Returns ``True`` if the SDK was actually initialised (or was already up
    from an earlier call), ``False`` if Sentry stays off because no DSN is
    set. The boolean is mostly useful for tests; production callers can
    ignore it.
    """
    global _SENTRY_INITIALISED
    cfg = settings or get_settings()
    if not cfg.sentry_dsn:
        log.debug("sentry disabled: SENTRY_DSN is empty")
        return False
    if _SENTRY_INITIALISED:
        return True

    # Imported lazily so backend code that does not need observability can
    # still be unit-tested without sentry-sdk installed (the package is in
    # requirements-backend.txt so prod always has it).
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    # arq integration ships in newer sentry-sdk; gracefully tolerate the
    # version where it is not yet available so we never break worker boot.
    integrations: list[Any] = [
        FastApiIntegration(transaction_style="endpoint"),
        StarletteIntegration(transaction_style="endpoint"),
        AsyncioIntegration(),
    ]
    try:
        from sentry_sdk.integrations.arq import ArqIntegration

        integrations.append(ArqIntegration())
    except Exception:  # pragma: no cover — depends on installed extras
        log.debug("arq integration not available; worker spans disabled")

    name = service_name or cfg.sentry_service_name
    sentry_sdk.init(
        dsn=cfg.sentry_dsn,
        environment=cfg.sentry_environment,
        traces_sample_rate=cfg.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=integrations,
        before_send=_before_send,
        # Surface a concise release marker so deploys are easy to bisect in
        # the Sentry UI. Operators can override SHIP_VERSION via env.
        release=_resolve_release(),
    )
    sentry_sdk.set_tag("service", name)
    _SENTRY_INITIALISED = True
    log.info(
        "sentry initialised service=%s environment=%s sample_rate=%s",
        name,
        cfg.sentry_environment,
        cfg.sentry_traces_sample_rate,
    )
    return True


def _resolve_release() -> str | None:
    """Derive a release tag for Sentry without importing app internals.

    Order of precedence: ``SHIP_VERSION`` env, ``VERSION`` file at repo root,
    or ``None`` (let Sentry auto-detect from git when running outside a
    container).
    """
    import os
    from pathlib import Path

    explicit = os.getenv("SHIP_VERSION", "").strip()
    if explicit:
        return f"ship@{explicit}"
    version_file = Path(__file__).resolve().parents[3] / "VERSION"
    if version_file.is_file():
        try:
            value = version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if value:
            return f"ship@{value}"
    return None


def _reset_for_tests() -> None:
    """Test helper — drop the cached "already initialised" guard."""
    global _SENTRY_INITIALISED
    _SENTRY_INITIALISED = False


def record_inbox_exception_breadcrumb(
    *,
    source: str,
    title: str,
    ticket_ref: str | None = None,
    **data: object,
) -> None:
    """Record exception-path inbox content without creating a row."""
    try:
        import sentry_sdk

        payload: dict[str, object] = {"source": source, **data}
        if ticket_ref:
            payload["ticket_ref"] = ticket_ref
        sentry_sdk.add_breadcrumb(
            category="inbox.exception",
            message=title[:200],
            level="info",
            data=payload,
        )
    except Exception:  # noqa: BLE001 — observability must not break callers
        log.debug("sentry breadcrumb skipped for inbox exception", exc_info=True)


__all__ = ["init_sentry", "record_inbox_exception_breadcrumb"]
