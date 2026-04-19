#!/usr/bin/env bash
# Ship cloud platform — first-run bootstrap.
#
# Drops a working .env into the repo root: random JWT_SECRET, generated Fernet
# ENCRYPTION_KEY, and Auth0 placeholders the operator must fill before going
# beyond local dev. Idempotent — re-running never overwrites existing values
# unless --force is passed.
#
# Usage:
#   ./scripts/bootstrap.sh                # populate missing keys, keep existing
#   ./scripts/bootstrap.sh --force        # regenerate JWT_SECRET / ENCRYPTION_KEY
#   ./scripts/bootstrap.sh --auth0 \
#       --domain my-tenant.eu.auth0.com \
#       --audience https://api.ship.local \
#       --client-id abc --client-secret xyz
#
# After this completes, run:
#   make up        # docker compose up -d --build
#   make health    # confirm backend + console respond

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/.env"
EXAMPLE_FILE="${ROOT}/.env.example"

FORCE=0
AUTH0_DOMAIN_VAL=""
AUTH0_AUDIENCE_VAL=""
AUTH0_CLIENT_ID_VAL=""
AUTH0_CLIENT_SECRET_VAL=""
SET_AUTH0=0

while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1 ;;
        --auth0) SET_AUTH0=1 ;;
        --domain) AUTH0_DOMAIN_VAL="$2"; shift ;;
        --audience) AUTH0_AUDIENCE_VAL="$2"; shift ;;
        --client-id) AUTH0_CLIENT_ID_VAL="$2"; shift ;;
        --client-secret) AUTH0_CLIENT_SECRET_VAL="$2"; shift ;;
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
    shift
done

# --- toolchain checks ----------------------------------------------------
need() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "[bootstrap] missing required tool: $1" >&2
        exit 3
    }
}
need docker
need openssl
need python3

if ! docker compose version >/dev/null 2>&1; then
    echo "[bootstrap] 'docker compose' (v2) is required; install Docker Desktop ≥ 4.0 or compose plugin" >&2
    exit 3
fi

# --- .env scaffolding ----------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$EXAMPLE_FILE" ]; then
        cp "$EXAMPLE_FILE" "$ENV_FILE"
        echo "[bootstrap] created .env from .env.example"
    else
        : > "$ENV_FILE"
        echo "[bootstrap] created empty .env (no .env.example found)"
    fi
fi

# Helper: read existing value (handles `KEY=value` and ignores comments).
get_env() {
    awk -F= -v key="$1" '
        $0 ~ "^[[:space:]]*"key"[[:space:]]*=" {
            sub("^[[:space:]]*"key"[[:space:]]*=", "");
            print
            exit
        }
    ' "$ENV_FILE"
}

# Helper: set/append a key. Skips blank assignments unless --force.
set_env() {
    local key="$1" value="$2" force="${3:-0}"
    local existing
    existing="$(get_env "$key")"
    if [ -n "$existing" ] && [ "$force" != "1" ]; then
        return 0
    fi
    if grep -Eq "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE"; then
        # POSIX-portable in-place edit (works on macOS sed and GNU sed).
        python3 -c "
import re, sys
path = sys.argv[1]; key = sys.argv[2]; value = sys.argv[3]
with open(path) as f: text = f.read()
pattern = re.compile(rf'^[ \t]*{re.escape(key)}[ \t]*=.*$', re.MULTILINE)
text = pattern.sub(f'{key}={value}', text, count=1)
with open(path, 'w') as f: f.write(text)
" "$ENV_FILE" "$key" "$value"
    else
        printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
    echo "[bootstrap] set $key"
}

# --- secrets -------------------------------------------------------------
JWT_SECRET_NEW="$(openssl rand -hex 32)"
ENCRYPTION_KEY_NEW="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' 2>/dev/null \
    || python3 -c 'import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"

set_env JWT_SECRET "$JWT_SECRET_NEW" "$FORCE"
set_env ENCRYPTION_KEY "$ENCRYPTION_KEY_NEW" "$FORCE"

# --- Auth0 placeholders --------------------------------------------------
# We keep SHIP_AUTH_MODE=local by default so devs can boot without Auth0.
# Once you fill these and flip SHIP_AUTH_MODE=auth0, the backend switches
# to JWKS verification and the console wires @auth0/nextjs-auth0.
if [ -z "$(get_env SHIP_AUTH_MODE)" ]; then
    set_env SHIP_AUTH_MODE "local"
fi
if [ -z "$(get_env AUTH0_DOMAIN)" ] || [ "$SET_AUTH0" = "1" ]; then
    set_env AUTH0_DOMAIN "${AUTH0_DOMAIN_VAL:-your-tenant.eu.auth0.com}" "$SET_AUTH0"
fi
if [ -z "$(get_env AUTH0_AUDIENCE)" ] || [ "$SET_AUTH0" = "1" ]; then
    set_env AUTH0_AUDIENCE "${AUTH0_AUDIENCE_VAL:-https://api.ship.local}" "$SET_AUTH0"
fi
if [ -z "$(get_env AUTH0_CLIENT_ID)" ] || [ "$SET_AUTH0" = "1" ]; then
    set_env AUTH0_CLIENT_ID "${AUTH0_CLIENT_ID_VAL:-replace-with-auth0-app-client-id}" "$SET_AUTH0"
fi
if [ -z "$(get_env AUTH0_CLIENT_SECRET)" ] || [ "$SET_AUTH0" = "1" ]; then
    set_env AUTH0_CLIENT_SECRET "${AUTH0_CLIENT_SECRET_VAL:-replace-with-auth0-app-client-secret}" "$SET_AUTH0"
fi
if [ -z "$(get_env AUTH0_SESSION_SECRET)" ]; then
    set_env AUTH0_SESSION_SECRET "$(openssl rand -hex 32)"
fi

# --- summary -------------------------------------------------------------
cat <<SUMMARY

[bootstrap] done. .env at: $ENV_FILE

Next steps:
  1. Review .env (especially AUTH0_* placeholders before flipping SHIP_AUTH_MODE).
  2. Bring the stack up:        make up
  3. Verify health:             make health
  4. Run the smoke test:        make smoke
  5. Open the console:          http://localhost:3001/login

Auth0 setup — full walkthrough at documentation/auth0-setup.md
  - Create an API + a Regular Web Application in your Auth0 tenant
  - Allowed Callback URLs: http://localhost:3001/auth/callback
  - Allowed Logout URLs:   http://localhost:3001
  - Run: scripts/bootstrap.sh --auth0 --domain ... --audience ... \\
                              --client-id ... --client-secret ...
  - Set SHIP_AUTH_MODE=auth0 in .env, then 'make restart'.
SUMMARY
