"""Connector-proxy fetcher registry (Phase 7c).

A **connector fetcher** is the glue between a live third-party source
(Notion page, Confluence space, Linear project, ServiceNow KB) and
the Distiller. It takes a decrypted integration token + a free-form
``resource_ref`` and returns one or more markdown pages the
Distiller can ingest.

Design goals
============

- **Registry + dispatcher**: each connector plugs in via
  :func:`register` and the ``/buckets/{slug}/sync`` endpoint calls
  :func:`fetch_connector_pages` without knowing which connector is
  wired. Adding Confluence/Linear later is a matter of creating a
  module that registers itself at import time.
- **Graceful fallback**: when no fetcher is registered for a kind,
  or the fetcher declares it cannot handle the given ``resource_ref``
  (e.g. Notion can handle ``{page_id}`` but not ``{database_id}``
  yet), we return an empty list and the caller falls back to the
  stub body. This keeps Phase 7b buckets working without forcing
  every connector to land at once.
- **Test-friendly**: fetchers are declared with a ``build`` factory
  that takes an optional ``httpx.AsyncClient`` so tests can inject a
  transport mock without monkey-patching httpx globals.

``fetch_connector_pages`` does NOT persist anything — it purely
returns a list of :class:`ConnectorPage` objects. Persistence
happens in the endpoint via :func:`ingest_connector_page` per page.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import httpx

from backend.app.db.models.tenancy import Integration


logger = logging.getLogger(__name__)


class ConnectorError(RuntimeError):
    """Base class for fetcher errors the endpoint should surface as 502."""


class ConnectorUnsupported(ConnectorError):
    """Raised when a fetcher explicitly cannot handle the resource_ref.

    Different from "no fetcher registered" (which is a silent miss +
    stub fallback). Raising this from a fetcher tells the caller
    "yes this is my integration kind, but this particular shape
    isn't implemented yet" — still triggers stub fallback, but with
    a logged warning so we know there's a shape we're skipping.
    """


class ConnectorConfigError(ConnectorError):
    """Raised when the integration row is unusable (missing secret, bad config)."""


@dataclass(frozen=True)
class ConnectorPage:
    """One fetched page ready for the Distiller.

    ``page_ref`` is embedded into the article's ``provenance`` and
    ``input_ref`` so later syncs can match up existing articles
    without scraping metadata out of the body; ``slug`` and ``title``
    drive the Distiller's slug/title hinting.
    """

    slug: str
    title: str
    body_md: str
    page_ref: Mapping[str, Any] = field(default_factory=dict)


# Signature for a fetcher. Takes the Integration row (so it can
# decrypt the secret only if the resource_ref is actually supported)
# and returns 0+ pages. Returning an empty list means "nothing to
# ingest" (e.g. empty database, skipped shape). Raising
# :class:`ConnectorUnsupported` triggers stub fallback with a
# warning. The reason fetchers take the whole row instead of a
# pre-decrypted token is so shape-checks happen before the
# ``secret_ciphertext`` -> ``token`` decrypt — otherwise an
# integration with a missing secret would 502 even when the
# resource_ref would have fallen back to stub anyway.
Fetcher = Callable[
    [Integration, Mapping[str, Any], httpx.AsyncClient | None],
    "list[ConnectorPage]",
]

_REGISTRY: dict[str, Fetcher] = {}

# Test-only transport override — the ``/sync`` endpoint doesn't take
# an ``http_client`` parameter (nothing in prod should inject one),
# so tests swap a ``MockTransport``-backed AsyncClient in here to
# deterministically simulate Notion/etc. without monkey-patching
# ``httpx.AsyncClient`` globally.
_http_client_override: httpx.AsyncClient | None = None


def set_http_client_override(client: httpx.AsyncClient | None) -> None:
    """Test-only: pin the AsyncClient used by fetchers when none is passed."""
    global _http_client_override
    _http_client_override = client


def register(kind: str) -> Callable[[Fetcher], Fetcher]:
    """Decorator — register ``fn`` as the fetcher for ``integration.kind``.

    Called at module import time. The canonical call site is
    :mod:`backend.app.services.connectors.notion`, which is imported
    eagerly by :func:`_ensure_loaded` below so the registry is
    populated before the first sync call.
    """

    def _decorate(fn: Fetcher) -> Fetcher:
        if kind in _REGISTRY:
            logger.warning(
                "connectors.register(%s): overwriting existing fetcher %r",
                kind,
                _REGISTRY[kind],
            )
        _REGISTRY[kind] = fn
        return fn

    return _decorate


def _ensure_loaded() -> None:
    """Import concrete connectors so their ``@register`` runs.

    Kept as a function (instead of a top-level import) to keep the
    module graph shallow and to let tests clear + re-register
    cleanly without dragging in every connector's httpx footprint.
    """
    for module in ("backend.app.services.connectors.notion",):
        try:
            importlib.import_module(module)
        except Exception:
            logger.exception("connectors: failed to import %s", module)


def get_fetcher(kind: str) -> Fetcher | None:
    """Return the registered fetcher for ``kind``, or None if none exists.

    Returning ``None`` (instead of raising) is what lets the caller
    fall back to the stub body for kinds we haven't wired yet.
    """
    _ensure_loaded()
    return _REGISTRY.get(kind)


async def fetch_connector_pages(
    integration: Integration,
    resource_ref: Mapping[str, Any],
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[ConnectorPage]:
    """High-level sync entry — resolve + invoke the registered fetcher.

    Returns the list of pages the endpoint should forward to
    :func:`ingest_connector_page`. An empty list is a legitimate
    "nothing changed, fall back to stub" signal and is NOT an error.

    Failure modes:

    - No fetcher registered → empty list + info log (stub fallback).
    - Fetcher raises :class:`ConnectorUnsupported` → empty list +
      warning log (stub fallback for unsupported resource_ref shape).
    - Fetcher raises :class:`ConnectorConfigError` / other
      :class:`ConnectorError` → re-raised for the caller to turn
      into a 502.
    """
    fetcher = get_fetcher(integration.kind)
    if fetcher is None:
        logger.info(
            "connectors: no fetcher for kind=%s — falling back to stub",
            integration.kind,
        )
        return []

    effective_client = http_client or _http_client_override
    try:
        pages = await _maybe_await(
            fetcher(integration, resource_ref, effective_client)
        )
    except ConnectorUnsupported as exc:
        logger.warning(
            "connectors.%s: unsupported resource_ref %r — %s (stub fallback)",
            integration.kind,
            dict(resource_ref),
            exc,
        )
        return []
    return list(pages)


async def _maybe_await(value: Any) -> Any:
    """Allow fetchers to be async functions or plain coroutines.

    Typed as ``Fetcher`` returning a list, but in practice every
    concrete fetcher is ``async def``, so the return type is a
    coroutine. We await it here rather than forcing every caller to
    remember which flavour they're touching.
    """
    if hasattr(value, "__await__"):
        return await value
    return value


def _reset_registry_for_tests() -> None:
    """Test helper — clear the registry so tests don't leak into each other."""
    _REGISTRY.clear()


__all__ = [
    "ConnectorConfigError",
    "ConnectorError",
    "ConnectorPage",
    "ConnectorUnsupported",
    "Fetcher",
    "fetch_connector_pages",
    "get_fetcher",
    "register",
    "set_http_client_override",
]
