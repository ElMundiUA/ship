#!/usr/bin/env bash
# Run autonomous pipeline for ONE ticket locally with full control.
# Usage: ./run-autonomous-local.sh ELM-61 [--no-agent] [--cloud] [--yes] [--verify-only]
#   --no-agent: skip agent step (create branch, you run agent manually)
#   --cloud: use Cursor Cloud Agents API (runs in cloud, avoids local agent -p hang)
#   --yes: skip commit confirmation (auto-commit and push)
#   --verify-only: run verify-setup + run-ticket-verify only (no branch/agent)
# Requires: .env with LINEAR_API_KEY, GITHUB_TOKEN; agent CLI for full run
set -euo pipefail

ISSUE="${1:?Usage: ./run-autonomous-local.sh ELM-XX [--no-agent] [--cloud] [--yes] [--verify-only]}"
SKIP_AGENT=false
USE_CLOUD=false
AUTO_YES=false
VERIFY_ONLY=false
for arg in "${@:2}"; do
  [[ "$arg" == "--no-agent" ]] && SKIP_AGENT=true
  [[ "$arg" == "--cloud" ]] && USE_CLOUD=true
  [[ "$arg" == "--yes" ]] && AUTO_YES=true
  [[ "$arg" == "--verify-only" ]] && VERIFY_ONLY=true
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$AGENT_DIR" && git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -z "$REPO_ROOT" ]] && REPO_ROOT="$(cd "$AGENT_DIR/.." && pwd)"
PROJECT_ROOT="$REPO_ROOT"

cd "$AGENT_DIR"
export $(grep -v '^#' "$REPO_ROOT/.env" 2>/dev/null | xargs) 2>/dev/null || true

if [[ "$VERIFY_ONLY" == true ]]; then
  echo "=== Verify-only mode: $ISSUE ==="
  bash "$SCRIPT_DIR/verify-setup.sh" --issue "$ISSUE"
  echo ""
  bash "$SCRIPT_DIR/run-ticket-verify.sh" "$ISSUE"
  exit 0
fi

echo "=== Autonomous run: $ISSUE ==="
echo ""

# 1) Get issue context
echo "--- Step 1: Get issue ---"
node dist/cli.js get "$ISSUE" --json 2>/dev/null > "$PROJECT_ROOT/issue-context.json" || true
if ! node dist/cli.js get "$ISSUE" &>/dev/null; then
  echo "❌ Issue $ISSUE not found"
  exit 1
fi

IS_BUG=$(node -e "try{const d=require('$PROJECT_ROOT/issue-context.json'); const labels=(d.labels?.nodes||[]).map((l)=>String(l?.name||'').toLowerCase()); const bug=labels.includes('flow:bug')||labels.includes('type:bug')||labels.includes('bug'); process.stdout.write(bug?'1':'0');}catch{process.stdout.write('0')}" 2>/dev/null)
BRANCH_PREFIX="feature"
COMMIT_TYPE="feat"
if [[ "$IS_BUG" == "1" ]]; then
  BRANCH_PREFIX="fix"
  COMMIT_TYPE="fix"
fi
BRANCH="${BRANCH_PREFIX}/${ISSUE}-auto"
echo "Resolved branch: $BRANCH"
echo "✅ Issue loaded"
echo ""

# 2) Start work (In Development + stage:dev)
echo "--- Step 2: Start work ---"
if ! node dist/cli.js status-set --issue "$ISSUE" --status "In Development"; then
  echo "In Development status unavailable; using In Progress fallback"
  node dist/cli.js status-set --issue "$ISSUE" --status "In Progress"
fi
node dist/cli.js label-add --issue "$ISSUE" --label "stage:dev"
echo ""

# 3) Create branch (skip for --cloud — Cloud Agent creates its own)
if [[ "$USE_CLOUD" == true ]]; then
  echo "--- Step 3: SKIPPED (--cloud: agent creates branch) ---"
else
# 3) Create branch from main (stash local changes to avoid committing them)
echo "--- Step 3: Create branch $BRANCH ---"
cd "$PROJECT_ROOT"
git fetch origin main 2>/dev/null || true
STASHED=false
if [[ -n $(git status -s) ]]; then
  echo "Stashing local changes before creating branch..."
  git stash push -u -m "pre-autonomous-$ISSUE" && STASHED=true
fi
if git show-ref --verify --quiet "refs/heads/$BRANCH" 2>/dev/null; then
  echo "Branch $BRANCH exists. Checking it out..."
  git checkout "$BRANCH"
  git pull origin "$BRANCH" 2>/dev/null || true
else
  git checkout -B "$BRANCH" origin/main
fi
echo "✅ On branch $BRANCH"
echo ""
fi

# 4) Run Cursor agent (or skip, or use Cloud)
if [[ "$SKIP_AGENT" == true ]]; then
  echo "--- Step 4: SKIPPED (--no-agent) ---"
  if [[ "${STASHED:-false}" == true ]]; then
    echo "Restoring stashed changes..."
    git stash pop 2>/dev/null || true
  fi
  echo "Run agent manually in Cursor, then:"
  echo "  cd $PROJECT_ROOT && git add -A && git status"
  echo "  git commit -m \"$COMMIT_TYPE($ISSUE): <description>\" && git push -u origin $BRANCH"
  echo "  cd runtime && node dist/cli.js pr-create -i $ISSUE"
  exit 0
fi

if [[ "$USE_CLOUD" == true ]]; then
  echo "--- Step 4: Launch Cursor Cloud Agent ---"
  "$SCRIPT_DIR/cloud-agent-launch.sh" "$ISSUE"
  echo ""
  echo "Cloud Agent launched. It will create branch ${BRANCH} and PR when done."
  echo "No local commit needed — agent runs in Cursor cloud."
  if [[ "${STASHED:-false}" == true ]]; then
    echo "Restoring stashed changes..."
    git stash pop 2>/dev/null || true
  fi
  exit 0
fi

echo "--- Step 4: Run Cursor agent (local) ---"
LOG_FILE="${AGENT_DIR}/artifacts/agent-run-${ISSUE}-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "${AGENT_DIR}/artifacts"
echo "Log: $LOG_FILE"
PROMPT=$(cat <<EOF
You are the Developer Agent for Linear issue ${ISSUE}.
Read issue context from issue-context.json in repo root.

Required steps:
1. Read spec/AC/technical notes from issue context.
2. Ensure issue state is In Development (fallback In Progress only if needed) and label stage:dev is present.
3. Implement the smallest safe fix on branch ${BRANCH}.
4. Add/update tests as needed.
5. Run validation from website/:
   - npm run lint
   - npm run typecheck
   - npm run test
   - npm run build
   - npm run test:e2e:smoke (only if available)
6. Do not proceed to PR if checks fail; fix first.

Rules:
- Only modify files needed.
- Do NOT create PR or push from this local agent step.
- If blocked, leave a clear issue comment and exit with failure.
EOF
)

# agent -p can hang on macOS (forum.cursor.com/150246). --force allows file edits. If it hangs, use GitHub Actions.
if ! agent -p --force "$PROMPT" 2>&1 | tee "$LOG_FILE"; then
  echo ""
  echo "⚠️ Agent exited with error. Check changes. You can:"
  echo "  - Fix manually and commit"
  echo "  - Or: git checkout . && exit"
fi
echo ""

# 5) Commit and push if changes
echo "--- Step 5: Commit and push ---"
cd "$PROJECT_ROOT"
if [[ -n $(git status -s) ]]; then
  git add -A
  git status
  DO_COMMIT=false
  if [[ "$AUTO_YES" == true ]]; then
    DO_COMMIT=true
  else
    read -p "Commit and push? [y/N] " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] && DO_COMMIT=true
  fi
  if [[ "$DO_COMMIT" == true ]]; then
    git commit -m "${COMMIT_TYPE}(${ISSUE}): autonomous implementation" || true
    git push -u origin "$BRANCH"
    echo ""
    echo "--- Step 6: Create PR ---"
    cd "$AGENT_DIR"
    node dist/cli.js pr-create -i "$ISSUE" 2>&1
    echo ""
    echo "✅ Done. Run verification: ./runtime/scripts/run-ticket-verify.sh $ISSUE"
  else
    echo "Skipped. Run manually when ready."
  fi
else
  echo "No changes. Run: node dist/cli.js comment -i $ISSUE -t 'No code changes produced.'"
fi
# Restore stashed changes after commit/push
if [[ "${STASHED:-false}" == true ]]; then
  echo ""
  echo "Restoring stashed changes..."
  git stash pop 2>/dev/null || echo "⚠️ Stash pop had conflicts - resolve manually"
fi
