#!/usr/bin/env bash
# Ship adoption launcher — thin wrapper around `npx @elmundi/ship-cli init`.
#
# Env (all optional):
#   SHIP_AGENTS=cursor,codex    # forwarded as --agents
#   SHIP_NONINTERACTIVE=1       # forwarded as --yes
#
# One-liner:
#   curl -fsSL https://raw.githubusercontent.com/ElMundiUA/ship/main/adopt-ship.sh | bash

set -euo pipefail

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Error: '$1' not found. Install Node.js 20+ (includes npm/npx). See https://nodejs.org/." >&2
    exit 1
  }
}
require node
require npx

ARGS=(--copy-playbook)
if [[ "${SHIP_NONINTERACTIVE:-0}" == "1" ]]; then ARGS+=(--yes); fi
if [[ -n "${SHIP_AGENTS:-}" ]]; then ARGS+=(--agents "$SHIP_AGENTS"); fi

if [[ ! -d .git ]]; then
  if [[ -z "$(ls -A . 2>/dev/null || true)" ]]; then
    echo "note: empty directory detected. 'shipctl new --here' is the intended entry point," >&2
    echo "      but that command does not exist yet (TODO). Falling through to 'shipctl init'." >&2
  else
    echo "note: no .git here. Running 'shipctl init' anyway; init does not require a git repo." >&2
  fi
fi

exec npx --yes @elmundi/ship-cli@latest init "${ARGS[@]}"
