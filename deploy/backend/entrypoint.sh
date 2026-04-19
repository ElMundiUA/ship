#!/usr/bin/env sh
# Ship backend container entrypoint.
#
# Runs Alembic migrations to head before handing off to the real CMD. Safe to
# run on every boot — Alembic is idempotent. Operators who want migrations
# decoupled from the API process (typical Helm setup with a Job) can set
# SHIP_SKIP_MIGRATIONS=1 and run alembic from a separate pod.

set -e

if [ "${SHIP_SKIP_MIGRATIONS:-0}" != "1" ]; then
    echo "[ship] running migrations…"
    # Log the resolved sync URL with the password redacted so operators can
    # confirm the driver hint at a glance when boot fails on a Postgres URL.
    python - <<'PY'
import re
from backend.app.core.config import get_settings
url = get_settings().sync_database_url
    print(f"[ship] alembic url = {re.sub(r'://([^:/?#]+):[^@]*@', r'://\1:***@', url)}")
PY
    alembic -c backend/alembic.ini upgrade head
fi

exec "$@"
