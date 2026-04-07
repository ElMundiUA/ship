#!/usr/bin/env bash
# Verify a ticket's pipeline state: PR status, release-check, preview URL.
# Usage: ./run-ticket-verify.sh ELM-XX [--release-check]
#   --release-check: run release-check (may change Linear state)
set -euo pipefail

ISSUE="${1:?Usage: ./run-ticket-verify.sh ELM-XX [--release-check]}"
RUN_RELEASE_CHECK=false
[[ "${2:-}" == "--release-check" ]] && RUN_RELEASE_CHECK=true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$AGENT_DIR/../.." && pwd)"

cd "$AGENT_DIR"
export $(grep -v '^#' .env 2>/dev/null | xargs) 2>/dev/null || true

echo "=== Ticket verification: $ISSUE ==="
echo ""

# 1) Linear issue
echo "--- Linear issue ---"
ISSUE_JSON=$(node dist/cli.js get "$ISSUE" --json 2>/dev/null || true)
if [[ -z "${ISSUE_JSON}" ]]; then
  echo "❌ Issue not found"
  exit 1
fi
echo "$ISSUE_JSON" | head -5
echo ""

# 2) PR for this issue (branch feature/ELM-XX-auto or fix/ELM-XX-auto)
IS_BUG=$(echo "$ISSUE_JSON" | node -e "try{const d=JSON.parse(require('fs').readFileSync(0,'utf8')); const labels=(d.labels?.nodes||[]).map((l)=>String(l?.name||'').toLowerCase()); const bug=labels.includes('flow:bug')||labels.includes('type:bug')||labels.includes('bug'); process.stdout.write(bug ? '1' : '0');}catch{process.stdout.write('0')}" 2>/dev/null)
if [[ "$IS_BUG" == "1" ]]; then
  BRANCH_PRIMARY="fix/${ISSUE}-auto"
  BRANCH_FALLBACK="feature/${ISSUE}-auto"
else
  BRANCH_PRIMARY="feature/${ISSUE}-auto"
  BRANCH_FALLBACK="fix/${ISSUE}-auto"
fi
REMOTE=$(cd "$PROJECT_ROOT" && git remote get-url origin 2>/dev/null | sed 's|git@github.com:|https://github.com/|;s|\.git$||')
OWNER=$(echo "$REMOTE" | sed 's|.*github.com/||;s|/.*||')
REPO=$(echo "$REMOTE" | sed 's|.*/||;s|\.git$||')

echo "--- PR status (branch: $BRANCH_PRIMARY) ---"
if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN not set, skipping PR lookup"
  PR_NUM=""
  BRANCH="$BRANCH_PRIMARY"
else
PR_NUM=""
BRANCH="$BRANCH_PRIMARY"
for CANDIDATE in "$BRANCH_PRIMARY" "$BRANCH_FALLBACK"; do
  TRY_NUM=$(curl -sS -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    "https://api.github.com/repos/${OWNER}/${REPO}/pulls?head=${OWNER}:${CANDIDATE}&state=open" 2>/dev/null | \
    node -e "try{const d=JSON.parse(require('fs').readFileSync(0,'utf8')); console.log(d[0]?d[0].number:'')}catch{console.log('')}" 2>/dev/null || true)
  if [[ -n "${TRY_NUM:-}" ]]; then
    PR_NUM="$TRY_NUM"
    BRANCH="$CANDIDATE"
    break
  fi
done
fi

if [[ -z "${PR_NUM:-}" ]]; then
  echo "No open PR for branches $BRANCH_PRIMARY or $BRANCH_FALLBACK"
  echo "  Create with: cd $PROJECT_ROOT && git checkout $BRANCH && cd $AGENT_DIR && node dist/cli.js pr-create -i $ISSUE"
else
  echo "PR #$PR_NUM on $BRANCH: https://github.com/${OWNER}/${REPO}/pull/${PR_NUM}"
  if [[ "$RUN_RELEASE_CHECK" == true ]]; then
    echo ""
    echo "--- Running release-check ---"
    node dist/cli.js release-check -i "$ISSUE" --pr "$PR_NUM" 2>&1
  else
    echo "  Run release-check: ./run-ticket-verify.sh $ISSUE --release-check"
  fi
fi

echo ""
echo "=== Done ==="
