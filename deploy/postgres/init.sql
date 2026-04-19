-- Ship cloud platform — Postgres bootstrap (RFC-0006).
-- Loaded by the official postgres image on first boot via /docker-entrypoint-initdb.d.
-- Idempotent: re-running is a no-op if the extensions already exist.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
