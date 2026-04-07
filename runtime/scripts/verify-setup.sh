#!/usr/bin/env bash
# Verify autonomous pipeline setup — run before debugging or first use.
# Usage: ./verify-setup.sh [--issue ELM-XX] [--skip-bunny]
#   --issue ELM-XX: also verify Linear issue exists and release-check
#   --skip-bunny: skip Bunny deploy check (no BUNNYNET_API_KEY needed)
set -euo pipefail

CHECK_ISSUE=""
SKIP_BUNNY=false
for i in "$@"; do
  [[ "$i" == "--issue" ]] && CHECK_ISSUE="next"
  [[ "$i" == "--skip-bunny" ]] && SKIP_BUNNY=true
  [[ "$i" =~ ^ELM-[0-9]+$ ]] && CHECK_ISSUE="$i"
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$AGENT_DIR" && git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -z "$REPO_ROOT" ]] && REPO_ROOT="$(cd "$AGENT_DIR/.." && pwd)"
PROJECT_ROOT="$REPO_ROOT"

cd "$AGENT_DIR"
export $(grep -v '^#' "$REPO_ROOT/.env" 2>/dev/null | xargs) 2>/dev/null || true

echo "=== Autonomous Pipeline Setup Verification ==="
echo ""

PASS=0
FAIL=0

check() {
  if "$@"; then
    echo "✅ $1"
    ((PASS++)) || true
    return 0
  else
    echo "❌ $1"
    ((FAIL++)) || true
    return 1
  fi
}

# 1) Linear API
echo "--- Linear ---"
if [[ -z "${LINEAR_API_KEY:-}" ]]; then
  echo "❌ Linear: LINEAR_API_KEY not set in .env"
  ((FAIL++)) || true
elif node dist/cli.js list -l 1 &>/dev/null; then
  echo "✅ Linear: API key valid, team accessible"
  ((PASS++)) || true
else
  echo "❌ Linear: API key invalid or team inaccessible"
  ((FAIL++)) || true
fi

# 2) GitHub token
echo ""
echo "--- GitHub ---"
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  REMOTE=$(cd "$PROJECT_ROOT" && git remote get-url origin 2>/dev/null | sed 's|git@github.com:|https://github.com/|;s|\.git$||')
  if [[ -n "$REMOTE" ]]; then
    OWNER=$(echo "$REMOTE" | sed 's|.*github.com/||;s|/.*||')
    REPO=$(echo "$REMOTE" | sed 's|.*/||;s|\.git$||')
    if curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/repos/${OWNER}/${REPO}" | grep -q '"id"'; then
      echo "✅ GitHub: token valid, repo accessible"
      ((PASS++)) || true
    else
      echo "❌ GitHub: token invalid or repo not accessible"
      ((FAIL++)) || true
    fi
  else
    echo "❌ GitHub: could not detect git remote"
    ((FAIL++)) || true
  fi
else
  echo "❌ GitHub: GITHUB_TOKEN not set in .env"
  ((FAIL++)) || true
fi

# 3) Cursor API (for --cloud)
echo ""
echo "--- Cursor Cloud Agents ---"
if [[ -n "${CURSOR_API_KEY:-}" ]]; then
  if [[ "${CURSOR_API_KEY}" =~ ^crsr_ ]]; then
    # Lightweight auth check: GET agents list (doesn't create anything)
    HTTP=$(curl -sS -o /tmp/cursor_verify.json -w "%{http_code}" \
      -u "${CURSOR_API_KEY}:" \
      "https://api.cursor.com/v0/agents?limit=1" 2>/dev/null || echo "000")
    if [[ "$HTTP" == "200" ]]; then
      echo "✅ Cursor: API key valid"
      ((PASS++)) || true
    elif [[ "$HTTP" == "401" || "$HTTP" == "403" ]]; then
      echo "❌ Cursor: API key invalid or expired"
      ((FAIL++)) || true
    else
      echo "⚠️  Cursor: HTTP $HTTP (key format ok, test launch to confirm)"
      ((PASS++)) || true
    fi
    rm -f /tmp/cursor_verify.json 2>/dev/null || true
  else
    echo "⚠️  Cursor: key should start with crsr_"
  fi
else
  echo "⚠️  Cursor: CURSOR_API_KEY not set (required for --cloud)"
fi

# 4) Bunny (optional)
echo ""
echo "--- Bunny PR Deploy ---"
if [[ "$SKIP_BUNNY" == true ]]; then
  echo "⏭️  Skipped (--skip-bunny)"
elif [[ -n "${BUNNYNET_API_KEY:-}" || -n "${BUNNY_MAIN_API_KEY:-}" ]]; then
  BKEY="${BUNNY_MAIN_API_KEY:-$BUNNYNET_API_KEY}"
  TOKEN=$(curl -sS -X POST 'https://api.bunny.net/apikey/exchange' \
    -H 'Content-Type: application/json' \
    -H "AccessKey: ${BKEY}" \
    --data "{\"AccessKey\":\"${BKEY}\"}" 2>/dev/null | node -e "try{console.log(JSON.parse(require('fs').readFileSync(0,'utf8')).Token)}catch{process.exit(1)}" 2>/dev/null || true)
  if [[ -n "$TOKEN" ]]; then
    CODE=$(curl -sS -o /dev/null -w "%{http_code}" \
      -H "Authorization: ${TOKEN}" \
      "https://api-mc.opsbunny.net/v1/namespaces/default/applications" 2>/dev/null || echo "000")
    if [[ "$CODE" == "200" ]]; then
      echo "✅ Bunny: API key valid, OpsBunny accessible"
      ((PASS++)) || true
    else
      echo "⚠️  Bunny: token ok but list apps returned HTTP $CODE"
    fi
  else
    echo "❌ Bunny: API key exchange failed"
    ((FAIL++)) || true
  fi
else
  echo "⚠️  Bunny: BUNNYNET_API_KEY not set (PR deploy will fail)"
fi

# 5) ship-agent CLI (dist/cli.js; npm package ship-agent, bin ship-agent / linear-agent)
echo ""
echo "--- ship-agent CLI ---"
if [[ -f "$AGENT_DIR/dist/cli.js" ]]; then
  echo "✅ ship-agent: dist/cli.js exists"
  ((PASS++)) || true
else
  echo "❌ ship-agent: dist/cli.js missing (run build)"
  ((FAIL++)) || true
fi

# 6) Optional: verify specific issue
if [[ -n "$CHECK_ISSUE" && "$CHECK_ISSUE" != "next" ]]; then
  echo ""
  echo "--- Issue $CHECK_ISSUE ---"
  if node dist/cli.js get "$CHECK_ISSUE" &>/dev/null; then
    echo "✅ Issue $CHECK_ISSUE exists"
    ((PASS++)) || true
  else
    echo "❌ Issue $CHECK_ISSUE not found"
    ((FAIL++)) || true
  fi
fi

echo ""
echo "=== Result: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
