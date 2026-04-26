#!/usr/bin/env bash
# Open Ship console in Playwright CLI with a persistent on-disk profile so
# Auth0 / app cookies survive across runs. First run: sign in once in the
# headed window. Later runs: same script reuses
#   output/playwright/chromium-profile/
# Override: SHIP_PW_PROFILE=/path/to/dir ./scripts/pw-open-console.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${SHIP_PW_PROFILE:-$ROOT/output/playwright/chromium-profile}"
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
PWCLI="${CODEX_HOME}/skills/playwright/scripts/playwright_cli.sh"
if [[ ! -f "$PWCLI" ]]; then
  echo "playwright skill wrapper missing: $PWCLI" >&2
  exit 1
fi
mkdir -p "$PROFILE"
URL="${1:-http://localhost:3001/}"
exec "$PWCLI" open "$URL" --headed --profile "$PROFILE"
