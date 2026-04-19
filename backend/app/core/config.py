"""Runtime configuration for the Ship backend (RFC-0006).

A single :class:`Settings` object is read from environment variables (and an
optional ``.env`` file at the repo root). The same shape is used in three
deployments — local docker-compose, self-hosted Helm, and our SaaS on Neon —
so the application code never branches on environment.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[3]


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

    # --- Git sync worker (RFC-0006) ---
    # On-disk root for cloned artifact repos. The worker materialises every
    # non-``file://`` ``ArtifactRepo`` under ``<root>/<workspace_id>/<repo_id>``
    # so the resolver can read it like a local layer.
    repo_cache_root: str = Field(
        default="/var/lib/ship/repo-cache", alias="REPO_CACHE_ROOT"
    )
    repo_sync_interval_minutes: int = Field(
        default=10, alias="REPO_SYNC_INTERVAL_MINUTES"
    )

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

        Operators can override with ``ALEMBIC_DATABASE_URL``; otherwise we map
        ``postgresql+asyncpg://`` → ``postgresql+psycopg://`` so a single
        ``DATABASE_URL`` works for both runtime and migrations.
        """
        if self.alembic_database_url:
            return self.alembic_database_url
        url = self.database_url
        if url.startswith("postgresql+asyncpg://"):
            return "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor; tests reset it via ``get_settings.cache_clear()``."""
    return Settings()
