#!/usr/bin/env bash
# Launch Cursor Cloud Agent via API (runs in Cursor cloud, no local worker — avoids agent -p hang).
# Usage: ./cloud-agent-launch.sh ELM-61
# Requires: CURSOR_API_KEY in .env (from cursor.com/dashboard?tab=integrations)
set -euo pipefail

ISSUE="${1:?Usage: ./cloud-agent-launch.sh ELM-XX}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$AGENT_DIR/../.." && pwd)"

cd "$AGENT_DIR"
export $(grep -v '^#' .env 2>/dev/null | xargs) 2>/dev/null || true

if [[ -z "${CURSOR_API_KEY:-}" ]]; then
  echo "❌ CURSOR_API_KEY required. Add to tools/linear-agent/.env"
  exit 1
fi

# Get issue details for prompt
ISSUE_JSON=$(node dist/cli.js get "$ISSUE" --json 2>/dev/null || echo "{}")
TITLE=$(echo "$ISSUE_JSON" | node -e "try{const d=JSON.parse(require('fs').readFileSync(0,'utf8')); console.log(d.title||'')}catch{console.log('')}" 2>/dev/null)
DESC=$(echo "$ISSUE_JSON" | node -e "try{const d=JSON.parse(require('fs').readFileSync(0,'utf8')); console.log((d.description||'').slice(0,6000))}catch{console.log('')}" 2>/dev/null)
ISSUE_URL=$(echo "$ISSUE_JSON" | node -e "try{const d=JSON.parse(require('fs').readFileSync(0,'utf8')); console.log(d.url||'')}catch{console.log('')}" 2>/dev/null)
IS_BUG=$(echo "$ISSUE_JSON" | node -e "try{const d=JSON.parse(require('fs').readFileSync(0,'utf8')); const labels=(d.labels?.nodes||[]).map((l)=>String(l?.name||'').toLowerCase()); const bug = labels.includes('flow:bug') || labels.includes('type:bug') || labels.includes('bug'); process.stdout.write(bug ? '1' : '0');}catch{process.stdout.write('0')}" 2>/dev/null)
BRANCH_PREFIX="feature"
if [[ "$IS_BUG" == "1" ]]; then
  BRANCH_PREFIX="fix"
fi
BRANCH="${BRANCH_PREFIX}/${ISSUE}-auto"

# Repo URL (https for API)
REMOTE=$(cd "$PROJECT_ROOT" && git remote get-url origin 2>/dev/null | sed 's|git@github.com:|https://github.com/|;s|\.git$||')
REPO_URL="${REMOTE:-https://github.com/}"

PROMPT=$(cat <<EOF
You are the Developer Agent.

Linear issue: ${ISSUE}
Issue URL: ${ISSUE_URL}
Title: ${TITLE}

Description/spec:
${DESC}

Global rules:
- Never merge PRs.
- Never mark an issue Done without explicit human approval.
- Prefer the smallest safe fix.
- Do not silently change product scope.
- If requirements are unclear, ask for clarification in the issue comments instead of guessing.
- If blocked by external infrastructure, stop and escalate clearly in the issue comments.
- Always leave a concise audit trail in Linear comments or PR comments.

Required steps:
1. Read the issue spec, acceptance criteria, and technical notes carefully.
2. Move issue status to "In Development". If that state does not exist, use "In Progress" and leave a comment explaining the fallback.
3. Add label "stage:dev".
4. Work on branch "${BRANCH}".
5. Implement the requested code changes only.
6. Add or update tests as needed for your changes.
7. Run locally before opening PR (from website/):
   - npm run lint
   - npm run typecheck
   - npm run test
   - npm run build
   - npm run test:e2e:smoke (only if available in package scripts)
8. Do NOT open PR if any command in step 7 fails; fix first.
9. When all checks pass, open PR with body:
   ## Summary
   ...

   ## Tests
   ...

   ## Deployment expectations
   ...

   ## Closes ${ISSUE}
10. Move issue status to "PR Opened" after PR creation.

Branch naming requirement:
- feature/${ISSUE}-auto for features
- fix/${ISSUE}-auto for bugs

Implementation constraints:
- Keep changes minimal and scoped.
- Do not broaden scope beyond the issue.
- If you cannot complete due to missing requirements or external outages, stop and document the blocker clearly on the issue.
EOF
)

PROMPT_FILE=$(mktemp)
trap "rm -f $PROMPT_FILE" EXIT
echo "$PROMPT" > "$PROMPT_FILE"

BODY=$(node -e "
const fs = require('fs');
const prompt = fs.readFileSync(process.argv[1], 'utf8');
const repo = process.argv[2];
const branch = process.argv[3];
console.log(JSON.stringify({
  prompt: { text: prompt },
  source: { repository: repo, ref: 'main' },
  target: {
    branchName: branch,
    autoCreatePr: true,
    openAsCursorGithubApp: false
  }
}));
" "$PROMPT_FILE" "$REPO_URL" "$BRANCH")

echo "Launching Cloud Agent for ${ISSUE} (branch: ${BRANCH})..."
RESP=$(curl -sS -X POST "https://api.cursor.com/v0/agents" \
  -u "${CURSOR_API_KEY}:" \
  -H "Content-Type: application/json" \
  -d "$BODY")

ID=$(echo "$RESP" | node -e "try{const d=JSON.parse(require('fs').readFileSync(0,'utf8')); console.log(d.id||'')}catch{console.log('')}" 2>/dev/null)
if [[ -z "$ID" ]]; then
  echo "❌ Failed to launch agent:"
  echo "$RESP" | head -20
  exit 1
fi

echo "✅ Cloud Agent launched: $ID"
echo "   Status: https://cursor.com/agents?id=$ID"
echo ""
echo "Agent runs in Cursor cloud. When done, it will create PR on branch ${BRANCH}."
echo "Poll status: curl -s -u KEY: https://api.cursor.com/v0/agents/$ID | jq .status"
