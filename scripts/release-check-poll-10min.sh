#!/usr/bin/env bash
# Poll release-check for up to 10 min. Exit 0 on success, 1 on timeout.
set -e
ISSUE="${1:-ELM-62}"
cd "$(dirname "$0")/.."
for i in $(seq 1 20); do
  echo "[$i/20] $(date +%H:%M:%S)"
  out=$(node dist/cli.js release-check --issue "$ISSUE" 2>&1) || true
  echo "$out"
  if echo "$out" | grep -q '"ok": true'; then
    echo "SUCCESS"
    exit 0
  fi
  [ $i -lt 20 ] && sleep 30
done
echo "TIMEOUT after 10 min"
exit 1
