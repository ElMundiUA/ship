"""Runtime configuration for the Ship backend (RFC-0006).

A single :class:`Settings` object is read from environment variables (and an
optional ``.env`` file at the repo root). The same shape is used in three
deployments — local docker-compose, self-hosted Helm, and our SaaS on Neon —
so the application code never branches on environment.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import base64
import binascii

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalize_pem(value: str | None) -> str | None:
    """Coerce a PEM-encoded secret into the multi-line form ``cryptography`` expects.

    Single-line env stores (Bunny Magic Containers, Fly.io secrets, k8s
    Secrets pasted via web UI) routinely strip newlines or refuse them
    altogether. We accept three on-the-wire shapes so operators never
    have to fight the input field:

    1. **Already a PEM** — left untouched.
    2. **Literal ``\\n`` escapes** — converted to real newlines (the same
       trick the GitHub Actions / Vercel docs recommend).
    3. **Base64 of the entire PEM block** — decoded. We probe by trying
       ``base64.b64decode(..., validate=True)`` and only accepting the
       result when it itself looks like a PEM, so a real one-liner PEM
       (which is technically also valid base64-ish input) is not
       mis-decoded.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if "-----BEGIN" in raw and "\n" in raw:
        return raw
    if "-----BEGIN" in raw and "\\n" in raw:
        return raw.replace("\\r\\n", "\n").replace("\\n", "\n")
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return raw
    if "-----BEGIN" in decoded:
        return decoded.strip()
    return raw


REPO_ROOT = Path(__file__).resolve().parents[3]


def _swap_driver(url: str, target_prefix: str) -> str:
    """Rewrite the URL scheme to ``target_prefix`` regardless of which Postgres
    spelling the operator pasted. Returns the URL unchanged if it doesn't look
    like a Postgres DSN at all (e.g. SQLite in tests)."""
    prefixes = (
        "postgresql+asyncpg://",
        "postgresql+psycopg2://",
        "postgresql+psycopg://",
        "postgresql://",
        "postgres://",
    )
    for prefix in prefixes:
        if url.startswith(prefix):
            return target_prefix + url[len(prefix) :]
    return url


_LIBPQ_ONLY_KEYS = frozenset({"sslmode", "channel_binding", "sslrootcert", "sslcert", "sslkey"})

# When ``AGENT_VENDOR=anthropic`` but operators only flip the vendor switch
# and leave ``AGENT_MODEL_*`` at the OpenAI defaults, Anthropic returns 404
# ("model: gpt-4o"). These ids match ``documentation/internal/agent-setup.md``.
#
# 2026-05-06: bumped fast model from ``-20250929`` → ``-20251001``. The
# ``-20250929`` snapshot was the Haiku 4.5 preview id; Anthropic retired it
# when the stable release shipped under ``-20251001``. Prod was returning
# 404 on every Haiku call — the router + claim extractor caught the
# exception and stamped ``no_fit`` / empty-claims accordingly, which is
# why the overnight reindex on Ship + Askslayer produced zero claims
# despite ``extracted_at`` being set on 50 source items.
_DEFAULT_ANTHROPIC_MODEL_MAIN = "claude-sonnet-4-5-20250929"
_DEFAULT_ANTHROPIC_MODEL_FAST = "claude-haiku-4-5-20251001"


def _looks_like_openai_chat_model_id(model: str) -> bool:
    """Return True when ``model`` is clearly an OpenAI chat id, not a Claude id."""
    m = model.strip().lower()
    if not m:
        return False
    if m.startswith("gpt-"):
        return True
    # OpenAI "omni" families — not valid on Anthropic.
    if m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return True
    return False


def _strip_libpq_only_params(url: str, *, for_asyncpg: bool) -> str:
    """Drop libpq-only query params from the URL when targeting asyncpg.

    Cloud Postgres providers (Neon, Supabase, …) hand operators DSNs that look
    like ``postgresql://...?sslmode=require&channel_binding=require``. Those
    are libpq parameters; psycopg understands them, but asyncpg parses the
    raw URL itself and rejects them at DSN-parse time. We strip them entirely
    on the asyncpg side and rely on
    :func:`asyncpg_connect_args` to push the TLS intent into ``connect_args``
    instead — that path uses asyncpg's native ``ssl=`` argument, which avoids
    the SQLAlchemy URL-coercion ambiguity around ``?ssl=true|require|...``.
    """
    if not for_asyncpg:
        return url
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _LIBPQ_ONLY_KEYS
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)
    )


def asyncpg_ssl_arg(original_database_url: str) -> str | bool | None:
    """Map the operator's ``DATABASE_URL`` to asyncpg's ``ssl=`` value.

    Returns:
        ``"require"`` when the DSN explicitly requested TLS via ``sslmode``,
        ``False`` when it explicitly disabled it, ``True`` for any non-loopback
        host (cloud Postgres always needs TLS, and assuming TLS by default
        keeps prod-safe behaviour for operators who forgot the parameter), and
        ``None`` for plain localhost dev DSNs (let asyncpg decide).
    """
    parts = urlsplit(original_database_url)
    sslmode: str | None = None
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() == "sslmode":
            sslmode = value.strip().lower()
            break
    if sslmode in ("disable",):
        return False
    if sslmode in ("allow", "prefer", "require", "verify-ca", "verify-full"):
        return sslmode
    host = (parts.hostname or "").lower()
    if host in ("", "localhost", "127.0.0.1", "::1"):
        return None
    return True


def _normalise_to_psycopg(url: str) -> str:
    """Force any Postgres URL to use the psycopg v3 sync driver.

    Alembic + SQLAlchemy 2.x default to ``psycopg2`` whenever the URL has no
    explicit driver hint, but the runtime image only installs psycopg v3
    (``psycopg[binary]``). Without this rewrite, an operator pasting their
    cloud-provided ``postgresql://...`` DSN crashes the boot with
    ``ModuleNotFoundError: No module named 'psycopg2'``.
    """
    swapped = _swap_driver(url, "postgresql+psycopg://")
    return _strip_libpq_only_params(swapped, for_asyncpg=False)


def _normalise_to_asyncpg(url: str) -> str:
    """Force any Postgres URL to use the asyncpg async driver.

    The runtime app uses asyncpg (the async dialect SQLAlchemy ships with),
    but operators paste DSNs in whatever spelling their cloud provider gave
    them (``postgresql://``, ``postgres://``, occasionally
    ``postgresql+psycopg2://``). Without the explicit ``+asyncpg`` hint
    SQLAlchemy tries to import psycopg2 and the request fans out into 500s.

    We also strip libpq-only query params (``sslmode``, ``channel_binding``)
    because asyncpg parses the raw URL itself and rejects them — see
    :func:`_strip_libpq_only_params`.
    """
    swapped = _swap_driver(url, "postgresql+asyncpg://")
    return _strip_libpq_only_params(swapped, for_asyncpg=True)


class Settings(BaseSettings):
    """All runtime knobs for ``ship-server`` and ``ship-worker``."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Identity ---
    public_url: str = Field(default="http://localhost:8100", alias="SHIP_PUBLIC_URL")
    # Browser-facing console origin. Used for OAuth-style callback redirects
    # that need to land back in the user's browser tab on the *console*
    # rather than the API. Defaults to the local Next.js dev server so the
    # docker-compose stack works out of the box.
    console_url: str = Field(default="http://localhost:3001", alias="SHIP_CONSOLE_URL")
    auth_mode: str = Field(default="local", alias="SHIP_AUTH_MODE")  # local | auth0
    allow_local_auth0_callbacks: bool = Field(
        default=False, alias="SHIP_ALLOW_LOCAL_AUTH0_CALLBACKS"
    )
    # E19 — laptop-offline profile. When ``True`` the tracker / code
    # host / CI resolvers short-circuit to the in-Postgres Memory
    # adapters under ``backend.app.integrations.local``, before any
    # native installation lookup. The ``.env.shared`` baseline turns
    # this on so the default laptop experience needs no external
    # accounts; production .env never sets it.
    use_memory_adapters: bool = Field(
        default=False, alias="SHIP_USE_MEMORY_ADAPTERS"
    )

    # --- Closed-beta invite gate (RFC-0011 / E08) ---
    # When ``True`` (production default) brand-new signups are gated through
    # the ``platform_invites`` table: a user without an open invite hits a
    # 403 at JIT-provisioning time. Disable in dev / self-hosted by setting
    # ``SHIP_INVITE_ONLY=false`` so the gate is a no-op.
    invite_only: bool = Field(default=True, alias="SHIP_INVITE_ONLY")
    # Soft cap on accepted invites surfaced to the public landing as
    # "X / N closed-beta seats taken". Read by ``/v1/public/beta-capacity``.
    closed_beta_cap: int = Field(default=50, alias="CLOSED_BETA_CAP")

    # --- E16 event-driven dispatcher (ELS-121..125) ---
    # Tracker poll interval. Backend pulls Linear updates every N
    # seconds per workspace with a ``linear`` integration; diff
    # against ``last_seen`` triggers ``ticket.transitioned`` events.
    # Default 300s prod, override to 60s for local dev so manual
    # Linear edits feed back into the dispatcher quickly.
    tracker_poll_interval_s: int = Field(
        default=300, alias="SHIP_TRACKER_POLL_INTERVAL_S"
    )
    # Per-workspace ceiling on concurrent dispatches when the
    # workspace's own ``max_concurrent_dispatches`` is NULL. Bounds the
    # blast radius of a knowledge-harvest fan-out that drops 50 new
    # tickets at once.
    default_workspace_dispatch_cap: int = Field(
        default=4, alias="SHIP_DEFAULT_WORKSPACE_DISPATCH_CAP"
    )
    # Shadow / fire toggle for the dispatcher. While ``False`` the
    # poller writes ``tracker.event.received`` rows to ``audit_log``
    # but does NOT call ``maybe_dispatch``; flip to ``True`` after we
    # trust the diff math against prod data (ELS-122 cutover step).
    tracker_poll_fire: bool = Field(
        default=False, alias="SHIP_TRACKER_POLL_FIRE"
    )
    # Global kill-switch for the notification seam (ELS-219). While
    # ``False`` the ``notify()`` router forces inbox-only regardless
    # of per-workspace channel routing — instant global rollback for
    # the Linear/email egress channels. Same shadow-then-fire pattern
    # as ``tracker_poll_fire`` above.
    notification_channels_enabled: bool = Field(
        default=False, alias="SHIP_NOTIFY_CHANNELS"
    )
    # Stall notifier (ELS-231): forward engine_health stalls through
    # notify() on a schedule. Off by default — the residue surface is
    # query-only until an operator opts into push.
    stall_notify_enabled: bool = Field(
        default=False, alias="SHIP_STALL_NOTIFY"
    )
    stall_notify_cooldown_minutes: int = Field(
        default=360, alias="SHIP_STALL_NOTIFY_COOLDOWN_MIN"
    )
    # StateProjector strangler (ELS-229): when on, every FSM->tracker
    # status write funnels through services/state_projector.py. Off
    # (default) keeps the original direct gateway.transition paths.
    state_projector_unified: bool = Field(
        default=False, alias="SHIP_STATE_PROJECTOR_UNIFIED"
    )

    # --- Auth0 (used when auth_mode == "auth0") ---
    auth0_domain: str | None = Field(default=None, alias="AUTH0_DOMAIN")
    auth0_audience: str | None = Field(default=None, alias="AUTH0_AUDIENCE")
    # Issuer is normally `https://{domain}/` but tenants on a custom domain
    # may emit a different `iss`; allow override.
    auth0_issuer: str | None = Field(default=None, alias="AUTH0_ISSUER")
    # JWKS URL override for tests (point at a local stub).
    auth0_jwks_url: str | None = Field(default=None, alias="AUTH0_JWKS_URL")

    # --- Database (Postgres + pgvector everywhere; RFC-0006) ---
    database_url: str = Field(
        default="postgresql+asyncpg://ship:ship@localhost:5433/ship",
        alias="DATABASE_URL",
    )
    # Alembic uses a sync driver; set explicitly so the same DATABASE_URL works
    # for the async app without forcing operators to maintain two URLs.
    alembic_database_url: str | None = Field(default=None, alias="ALEMBIC_DATABASE_URL")

    # --- Cache / broker / pubsub ---
    # Optional. Only the ARQ worker reads it; cloud SaaS topology has no worker
    # container so REDIS_URL stays unset there. The in-memory rate limiter on
    # the API side has no Redis dependency.
    redis_url: str | None = Field(default=None, alias="REDIS_URL")

    # --- Object storage (documents bucket source files) ---
    s3_endpoint_url: str | None = Field(default=None, alias="S3_ENDPOINT_URL")
    s3_bucket: str = Field(default="ship-documents", alias="S3_BUCKET")
    s3_access_key: str | None = Field(default=None, alias="S3_ACCESS_KEY")
    s3_secret_key: str | None = Field(default=None, alias="S3_SECRET_KEY")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")

    # --- Lighthouse (per-workspace knowledge engine, whafr) ---
    # When unset, the knowledge-search path stays on Ship's internal
    # index. Set the base URL to route reads at the per-workspace
    # Lighthouse instance (the engine scopes by the X-Workspace header).
    # The admin token gates importer provisioning; retrieval is open.
    lighthouse_base_url: str | None = Field(default=None, alias="LIGHTHOUSE_BASE_URL")
    lighthouse_admin_token: str | None = Field(
        default=None, alias="LIGHTHOUSE_ADMIN_TOKEN"
    )

    # --- Auth / secrets ---
    jwt_secret: str = Field(default="dev-only-not-for-prod-change-me", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_ttl_seconds: int = Field(default=60 * 60 * 12, alias="JWT_TTL_SECONDS")
    encryption_key: str | None = Field(default=None, alias="ENCRYPTION_KEY")

    # --- Observability (Sentry, RFC-0006 phase 2) ---
    # When ``sentry_dsn`` is empty we skip ``sentry_sdk.init`` entirely, so a
    # missing key is the documented "Sentry off" mode for laptop dev. The
    # release / environment values are read by Sentry to group events.
    sentry_dsn: str | None = Field(default=None, alias="SENTRY_DSN")
    sentry_environment: str = Field(
        default="local", alias="SENTRY_ENVIRONMENT"
    )
    # Performance sampling. We default to 0.1 in prod and 0.0 in local so a
    # mistakenly-set DSN on a developer laptop does not flood the project.
    sentry_traces_sample_rate: float = Field(
        default=0.0, alias="SENTRY_TRACES_SAMPLE_RATE", ge=0.0, le=1.0
    )
    # Logical service name attached as a tag; lets one Sentry project hold
    # both ``ship-server`` and ``ship-worker`` events without confusion.
    sentry_service_name: str = Field(
        default="ship-server", alias="SENTRY_SERVICE_NAME"
    )

    # --- Existing methodology API knobs (kept for backwards compatibility) ---
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_embed_model: str = Field(
        default="text-embedding-3-small", alias="OPENAI_EMBED_MODEL"
    )
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    feedback_repo: str = Field(default="ElMundiUA/ship", alias="SHIP_FEEDBACK_REPO")

    # --- Feature flags -------------------------------------------------------
    # When False (default in prod), only Linear and GitHub Issues are offered
    # in the tracker picker. When True, partial integrations (Notion, Jira,
    # Asana, ClickUp, Monday, Spreadsheet) also appear marked as "Coming soon".
    enable_partial_trackers: bool = Field(
        default=False, alias="SHIP_ENABLE_PARTIAL_TRACKERS"
    )
    # Warn-only: surface sibling open-PR file overlap before dev_implementation.
    enable_file_overlap_warnings: bool = Field(
        default=False, alias="SHIP_ENABLE_FILE_OVERLAP_WARNINGS"
    )

    # --- Agent (C12) ----------------------------------------------------------
    # Pilot ships OpenAI as the default backend because ``OPENAI_API_KEY`` is
    # already wired for embeddings; Anthropic is plugged in behind the same
    # :class:`AgentClient` abstraction and only activates when
    # ``ANTHROPIC_API_KEY`` is present. Switching vendor is one env flip —
    # ``AGENT_VENDOR=anthropic`` — so B10 (BYO per-tenant keys) can layer on
    # top later without reshuffling call sites.
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    agent_vendor: str = Field(default="openai", alias="AGENT_VENDOR")
    # Primary model drives the user-facing conversation; ``fast`` drives
    # cheap one-shot chores (topic-shift classifier, summarisers) where we
    # do not want to burn frontier-tier tokens.
    agent_model_main: str = Field(default="gpt-4o", alias="AGENT_MODEL_MAIN")
    agent_model_fast: str = Field(default="gpt-4o-mini", alias="AGENT_MODEL_FAST")

    # --- Deploy planner: LOCAL-DEV Gemini fallback (DO NOT DEPLOY) --------
    # ⚠️  TEMPORARY local-only escape hatch. The deploy planner normally
    # uses Ship's configured agent vendor (OpenAI/Anthropic via
    # ``pick_default_client``). On a laptop that has neither key, set
    # ``DEPLOY_PLANNER_GEMINI_API_KEY`` to a personal Gemini key so the
    # planner can run end-to-end locally. This path is GATED behind
    # ``DEPLOY_PLANNER_ALLOW_DEV_FALLBACK=true`` and MUST stay off in any
    # deployed environment. The key value lives only in your gitignored
    # ``.env`` — never commit it. See DEPLOY_SETUP.md.
    deploy_planner_gemini_api_key: str | None = Field(
        default=None, alias="DEPLOY_PLANNER_GEMINI_API_KEY"
    )
    deploy_planner_gemini_model: str = Field(
        default="gemini-2.5-flash", alias="DEPLOY_PLANNER_GEMINI_MODEL"
    )
    deploy_planner_allow_dev_fallback: bool = Field(
        default=False, alias="DEPLOY_PLANNER_ALLOW_DEV_FALLBACK"
    )
    # Optional backend-side key for the Mistral deploy-planner vendor.
    # Used both for BYO planning and for the live model-list lookup that
    # populates the "New deployment" model dropdown (see
    # ``services.deploy.model_catalog``). Repo GitHub-secret keys can't be
    # read back from GitHub, so the dropdown relies on this env key.
    mistral_api_key: str | None = Field(default=None, alias="MISTRAL_API_KEY")
    # Platform-owned Cursor key, used ONLY to populate the per-stage model
    # dropdown for Cursor stages (``services.deploy.model_catalog`` →
    # api.cursor.com/v1/models). Cursor has no keyless catalogue (models.dev
    # doesn't list it) and its API 401s without a key, so the picker needs
    # this. Read-only listing only — execution keeps using the repo's own
    # write-only GitHub CURSOR_API_KEY secret, never this one.
    cursor_api_key: str | None = Field(default=None, alias="CURSOR_API_KEY")
    # Hard cap on tokens we let the agent spend per turn (prompt + completion
    # combined). Triggers a 429 from the SSE route when exceeded so a runaway
    # tool-loop doesn't silently nuke a tenant's monthly budget.
    agent_max_tokens_per_turn: int = Field(
        default=200_000, alias="AGENT_MAX_TOKENS_PER_TURN"
    )

    # --- GitHub App "Ship" (pilot WOW-onboarding flow, RFC pilot-plan) ---
    # Numeric App ID shown on the "About" page of the App settings.
    github_app_id: str | None = Field(default=None, alias="GITHUB_APP_ID")
    # PEM-encoded private key (the whole ``-----BEGIN RSA PRIVATE KEY-----``
    # block). Required to mint the App-level JWT used to exchange for
    # short-lived per-installation tokens.
    #
    # Single-line env stores (Bunny Magic Containers, Fly.io secrets,
    # k8s Secret YAML pasted via web UI) reject the raw multi-line PEM,
    # so we also accept ``\n``-escaped strings and base64 of the whole
    # block. See ``_normalize_pem`` for the decode logic.
    github_app_private_key: str | None = Field(
        default=None, alias="GITHUB_APP_PRIVATE_KEY"
    )

    @field_validator("github_app_private_key", mode="before")
    @classmethod
    def _decode_github_app_private_key(cls, value: object) -> object:
        if value is None or isinstance(value, str):
            return _normalize_pem(value)
        return value
    # OAuth client_id / client_secret of the **App** (separate from the
    # Auth0-side GitHub social-login OAuth app — see pilot-plan.md). Used to
    # identify the installing user during the install callback.
    github_app_client_id: str | None = Field(
        default=None, alias="GITHUB_APP_CLIENT_ID"
    )
    github_app_client_secret: str | None = Field(
        default=None, alias="GITHUB_APP_CLIENT_SECRET"
    )
    # HMAC-SHA256 secret configured on the App's webhook page; we reject any
    # delivery whose ``X-Hub-Signature-256`` does not verify under this key.
    github_app_webhook_secret: str | None = Field(
        default=None, alias="GITHUB_APP_WEBHOOK_SECRET"
    )
    # The exact slug GitHub uses in the install URL
    # (``https://github.com/apps/<slug>/installations/new``). Optional —
    # when unset (the default) the backend looks the slug up live from
    # ``GET https://api.github.com/app`` using the App's own JWT and
    # caches it for an hour. Set this only when you want to pin the slug
    # explicitly (staging Apps, integration tests, etc.).
    github_app_slug: str | None = Field(default=None, alias="GITHUB_APP_SLUG")

    # --- Linear OAuth (pilot Day 2 — tracker WOW flow) ---
    # Public OAuth Application credentials registered at
    # https://linear.app/<workspace>/settings/api/applications. Required
    # for the install/start + install/callback endpoints; without them
    # the routes 503 so ops sees a clean misconfiguration error.
    linear_client_id: str | None = Field(default=None, alias="LINEAR_CLIENT_ID")
    linear_client_secret: str | None = Field(
        default=None, alias="LINEAR_CLIENT_SECRET"
    )
    # Shared HMAC-SHA256 secret used to verify Linear webhook deliveries.
    # One value per backend install (not per workspace) — the webhook
    # handler maps payload → workspace via the ``data.team.id`` Linear
    # ships with every event, looking up the matching Integration row.
    # Operators copy this value into Linear's webhook UI when wiring
    # the hook (or set it via ``webhookCreate`` GraphQL mutation).
    # Unset → webhook endpoint fails closed (rejects every delivery).
    linear_webhook_secret: str | None = Field(
        default=None, alias="LINEAR_WEBHOOK_SECRET"
    )
    # Comma-separated OAuth scopes. ``read,write`` is the minimum the
    # pilot adapter needs (list issues + post comments); ``admin`` is
    # required for ``webhookCreate`` / ``webhookDelete`` mutations
    # which Linear gates behind workspace-admin even when the OAuth
    # user is one. Without ``admin`` the programmatic webhook
    # provisioning endpoint (``POST .../webhook/provision``) returns
    # ``Linear GraphQL error: Invalid role: admin required``.
    # Workspaces that connected Linear before this default bump need
    # to reconnect to pick up the new scope; their existing token
    # stays read+write-only until they do.
    linear_oauth_scopes: str = Field(
        default="read,write,admin,issues:create,comments:create",
        alias="LINEAR_OAUTH_SCOPES",
    )
    # How often the ``linear_token_refresh_tick`` cron rotates each
    # workspace's access+refresh pair via Linear's
    # ``grant_type=refresh_token`` flow. Default 6h is well below
    # Linear's documented expiry floor for ``actor=user`` apps and
    # leaves four refresh attempts per day if Linear ever rotates
    # mid-cadence.
    linear_token_refresh_hours: int = Field(
        default=6,
        ge=1,
        le=72,
        alias="LINEAR_TOKEN_REFRESH_HOURS",
    )

    # Ship-side trigger-workflow scheduler. GHA throttles ``schedule:``
    # events under load (4-8h gaps in production); a backend cron that
    # dispatches via ``workflow_dispatch`` gives operators a
    # deterministic cadence. 5-field crontab expression, UTC. Default
    # ``0 * * * *`` = every hour at :00 — pairs with the customer
    # workflow's ``SHIP_MAX_ACTIONS_PER_TICK=4`` (4 × ~4-7 min ≈
    # 16-28 min, fits the 45-min runner budget with headroom).
    workflow_dispatch_cron_expr: str = Field(
        default="0 * * * *",
        alias="WORKFLOW_DISPATCH_CRON_EXPR",
    )

    # --- Notion OAuth (pilot Day 2 — tracker WOW flow) ---
    # Public Notion integration credentials from
    # https://www.notion.so/profile/integrations . Notion calls these
    # ``OAuth Client ID`` / ``OAuth Client Secret`` in the UI; same
    # shape as Linear. The user still has to share at least one database
    # with the integration after the OAuth dance — Notion's API does not
    # let us list every database the user can see, only ones explicitly
    # granted to the integration. The console explains this in copy.
    notion_client_id: str | None = Field(
        default=None, alias="NOTION_CLIENT_ID"
    )
    notion_client_secret: str | None = Field(
        default=None, alias="NOTION_CLIENT_SECRET"
    )

    # --- DigitalOcean OAuth (deploy provider — App Platform) -------------
    # Public OAuth Application credentials registered at
    # https://cloud.digitalocean.com/account/api/applications . Required
    # for the deploy ``connect/start`` + ``connect/callback`` endpoints;
    # without them the routes 503 so ops sees a clean misconfiguration
    # error (same posture as Linear/Notion).
    digitalocean_client_id: str | None = Field(
        default=None, alias="DIGITALOCEAN_CLIENT_ID"
    )
    digitalocean_client_secret: str | None = Field(
        default=None, alias="DIGITALOCEAN_CLIENT_SECRET"
    )
    # Space-separated OAuth scopes. ``write`` is required to create and
    # manage App Platform apps; ``read`` lets us poll deployment status.
    # DigitalOcean only offers these two coarse scopes today.
    digitalocean_oauth_scopes: str = Field(
        default="read write",
        alias="DIGITALOCEAN_OAUTH_SCOPES",
    )
    # DigitalOcean access tokens expire after 30 days and ship a
    # single-use refresh token. This cron cadence rotates the
    # access+refresh pair well before expiry; 24h leaves ~29 days of
    # head-room and many retry windows before a token would ever lapse.
    digitalocean_token_refresh_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        alias="DIGITALOCEAN_TOKEN_REFRESH_HOURS",
    )

    # --- Telegram bot adapter (group ↔ workspace bridge) ------------------
    # Both must be set for the bot worker to start; absent values keep the
    # bot dormant so the same image can ship with or without the adapter
    # configured. ``telegram_bot_username`` is the public ``@handle`` (no
    # leading ``@``) — used to build the deep-link
    # ``https://t.me/<username>?startgroup=<nonce>`` shown in the Console
    # bind page.
    telegram_bot_token: str | None = Field(
        default=None, alias="TELEGRAM_BOT_TOKEN"
    )
    telegram_bot_username: str | None = Field(
        default=None, alias="TELEGRAM_BOT_USERNAME"
    )

    # --- Email / Twilio SendGrid -------------------------------------------
    # Email is opt-in: when ``email_provider`` is ``"log"`` (default) the
    # backend renders messages and writes them to the application log
    # without contacting any vendor — this keeps laptop dev and the
    # entire test suite credential-free. Flip to ``"sendgrid"`` and set
    # the API key + verified sender to actually deliver mail. ``"none"``
    # is a kill switch: the API surface stays intact but every send is
    # a no-op (used by tests that should not even render templates).
    email_provider: str = Field(default="log", alias="EMAIL_PROVIDER")
    sendgrid_api_key: str | None = Field(
        default=None, alias="SENDGRID_API_KEY"
    )
    # Must be a sender already verified in the SendGrid dashboard
    # (Settings → Sender Authentication). SendGrid rejects unverified
    # senders with HTTP 403 at send time, not at API-key validation.
    sendgrid_from_email: str | None = Field(
        default=None, alias="SENDGRID_FROM_EMAIL"
    )
    sendgrid_from_name: str = Field(
        default="Ship", alias="SENDGRID_FROM_NAME"
    )
    sendgrid_reply_to: str | None = Field(
        default=None, alias="SENDGRID_REPLY_TO"
    )
    # SendGrid's REST API has no nativ async client, so the SendGridSender
    # offloads the blocking call to a worker thread. This is the timeout
    # for that call; the FastAPI request that triggered the send already
    # ran in a BackgroundTask, so a small bound here is fine.
    email_send_timeout_seconds: float = Field(
        default=10.0, alias="EMAIL_SEND_TIMEOUT_SECONDS", gt=0.0
    )

    # --- Firecrawl (website knowledge source) ---
    firecrawl_api_key: str | None = Field(default=None, alias="FIRECRAWL_API_KEY")
    firecrawl_api_url: str = Field(
        default="https://api.firecrawl.dev/v1", alias="FIRECRAWL_API_URL"
    )

    @model_validator(mode="after")
    def _validate_email_provider(self) -> "Settings":
        """Refuse to start in ``email_provider=sendgrid`` without credentials.

        We catch this at boot rather than at first-send so a misconfigured
        prod deploy fails with an obvious error instead of silently
        eating outgoing invites.
        """
        provider = (self.email_provider or "log").lower().strip()
        if provider == "sendgrid":
            missing = [
                name
                for name, value in (
                    ("SENDGRID_API_KEY", self.sendgrid_api_key),
                    ("SENDGRID_FROM_EMAIL", self.sendgrid_from_email),
                )
                if not (value or "").strip()
            ]
            if missing:
                raise ValueError(
                    "EMAIL_PROVIDER=sendgrid requires "
                    + ", ".join(missing)
                    + ". Set them or fall back to EMAIL_PROVIDER=log."
                )
        elif provider not in {"log", "none"}:
            raise ValueError(
                f"unknown EMAIL_PROVIDER {self.email_provider!r}; "
                "expected one of: sendgrid, log, none"
            )
        return self

    @model_validator(mode="after")
    def _no_localhost_urls_in_cloud(self) -> "Settings":
        """Refuse to start in cloud mode with localhost-shaped public URLs.

        ``SHIP_PUBLIC_URL`` and ``SHIP_CONSOLE_URL`` are baked into every
        OAuth callback the backend hands to GitHub / Linear / Notion, plus
        the redirect target on the install callback. Forgetting to set
        them in the cloud env used to silently fall back to the local
        Next.js dev defaults — so the install completed, then GitHub
        bounced the user to ``http://localhost:3001/...`` and the WOW
        onboarding ended on a "site can't be reached" tab.

        We can't do that to operators, so cloud (``SHIP_AUTH_MODE=auth0``)
        bootstrap fails fast with a clear message. The direct laptop dev
        targets set ``SHIP_ALLOW_LOCAL_AUTH0_CALLBACKS=true`` so engineers can
        reuse the shared dev Auth0 tenant while the console and backend run on
        localhost.
        """
        if self.auth_mode != "auth0":
            return self
        if self.allow_local_auth0_callbacks:
            return self
        offenders: list[str] = []
        for env_name, value in (
            ("SHIP_PUBLIC_URL", self.public_url),
            ("SHIP_CONSOLE_URL", self.console_url),
        ):
            host = value.lower()
            if "localhost" in host or "127.0.0.1" in host or host.startswith("http://0.0.0.0"):
                offenders.append(f"{env_name}={value!r}")
        if offenders:
            raise ValueError(
                "Cloud (auth_mode=auth0) cannot use localhost URLs for the "
                "OAuth callback origins. Set the following env vars to your "
                "real public hostnames and redeploy: " + ", ".join(offenders)
            )
        return self

    @model_validator(mode="after")
    def _anthropic_models_not_openai_ids(self) -> "Settings":
        """If Anthropic is the active vendor, never send OpenAI model ids upstream.

        ``AGENT_MODEL_MAIN`` / ``AGENT_MODEL_FAST`` default to ``gpt-4o`` /
        ``gpt-4o-mini`` for the OpenAI path. Operators who set
        ``AGENT_VENDOR=anthropic`` + ``ANTHROPIC_API_KEY`` often forget to
        retarget the model env vars; Anthropic then 404s with
        ``model: gpt-4o``. Swap only when we are sure the Anthropic client will
        be selected (vendor + key). If vendor is ``anthropic`` but the key is
        missing, :func:`pick_default_client` falls back to OpenAI — leave
        OpenAI-shaped defaults intact in that case.
        """
        vendor = (self.agent_vendor or "openai").lower().strip()
        if vendor != "anthropic" or not (self.anthropic_api_key or "").strip():
            return self
        if _looks_like_openai_chat_model_id(self.agent_model_main):
            self.agent_model_main = _DEFAULT_ANTHROPIC_MODEL_MAIN
        if _looks_like_openai_chat_model_id(self.agent_model_fast):
            self.agent_model_fast = _DEFAULT_ANTHROPIC_MODEL_FAST
        return self

    @property
    def resolved_auth0_issuer(self) -> str | None:
        """Default issuer to ``https://{domain}/`` when not overridden."""
        if self.auth0_issuer:
            return self.auth0_issuer
        if self.auth0_domain:
            return f"https://{self.auth0_domain}/"
        return None

    @property
    def resolved_auth0_jwks_url(self) -> str | None:
        if self.auth0_jwks_url:
            return self.auth0_jwks_url
        if self.auth0_domain:
            return f"https://{self.auth0_domain}/.well-known/jwks.json"
        return None

    @property
    def sync_database_url(self) -> str:
        """Return a sync-driver URL suitable for Alembic.

        Always normalises to ``postgresql+psycopg://`` (psycopg v3) — that's
        the only sync DBAPI we ship in ``requirements-backend.txt``. We accept
        ``DATABASE_URL`` or ``ALEMBIC_DATABASE_URL`` in any of the common
        Postgres URL spellings (``postgres://``, ``postgresql://`` with no
        explicit driver, ``postgresql+asyncpg://``, ``postgresql+psycopg2://``)
        so operators can paste the connection string their cloud provider
        hands out without remembering which driver hint we expect.
        """
        raw = (self.alembic_database_url or self.database_url).strip()
        return _normalise_to_psycopg(raw)

    @property
    def async_database_url(self) -> str:
        """Return an async-driver URL suitable for the runtime SQLAlchemy
        engine. See :func:`_normalise_to_asyncpg` — the same forgiving
        rewrite story as ``sync_database_url`` but targeting asyncpg.
        """
        return _normalise_to_asyncpg(self.database_url.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor; tests reset it via ``get_settings.cache_clear()``."""
    return Settings()
