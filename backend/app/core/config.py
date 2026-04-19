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

from pydantic import Field, field_validator
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
    # Comma-separated OAuth scopes. ``read,write`` is the minimum the
    # pilot adapter needs (list issues + post comments). Leave the
    # defaults unless a tenant explicitly objects.
    linear_oauth_scopes: str = Field(
        default="read,write,issues:create,comments:create",
        alias="LINEAR_OAUTH_SCOPES",
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
