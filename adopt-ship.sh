#!/usr/bin/env bash
# Ship adoption launcher — run from your *product* repository root.
#
# One-liner (review script on GitHub before piping to bash):
#   curl -fsSL https://raw.githubusercontent.com/ElMundiUA/ship/main/adopt-ship.sh | bash
#
# Env (optional, non-interactive):
#   SHIP_NONINTERACTIVE=1    (skip confirmations; default ElMundi = no)
#   SHIP_AGENT=cursor|claude
#   SHIP_ADD_SUBMODULE=0|1   (default: 1 when playbook missing)
#   SHIP_ELMUNDI=0|1         (append ElMundi addendum to agent instructions)
#   SHIP_REPO_URL, SHIP_SUBDIR, SHIP_BRANCH

set -euo pipefail

SHIP_REPO_URL="${SHIP_REPO_URL:-https://github.com/ElMundiUA/ship.git}"
SHIP_SUBDIR="${SHIP_SUBDIR:-tools/ship}"
SHIP_BRANCH="${SHIP_BRANCH:-main}"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "Error: not inside a git repository. cd to your product repo and run again." >&2
  exit 1
}

cd "$REPO_ROOT"

playbook_generic="$REPO_ROOT/$SHIP_SUBDIR/prompts/onboarding/adopt-ship-generic.md"
playbook_elmundi="$REPO_ROOT/$SHIP_SUBDIR/prompts/onboarding/adopt-ship-elmundi.md"

ensure_submodule() {
  if [[ -f "$playbook_generic" ]]; then
    return 0
  fi

  local add_default="1"
  if [[ -n "${SHIP_ADD_SUBMODULE:-}" ]]; then
    add_default="$SHIP_ADD_SUBMODULE"
  fi

  local ans="y"
  if [[ "$add_default" != "1" ]]; then
    echo "Ship playbook not found at $playbook_generic"
    read -r -p "Add Ship as git submodule at ${SHIP_SUBDIR}? [y/N] " ans || true
    ans="${ans:-n}"
  elif [[ -z "${SHIP_NONINTERACTIVE:-}" ]]; then
    echo "Ship playbook not found. Will add submodule: $SHIP_SUBDIR ← $SHIP_REPO_URL (branch $SHIP_BRANCH)"
    read -r -p "Continue? [Y/n] " ans || true
    ans="${ans:-Y}"
  else
    # Non-interactive + default add — proceed without prompt
    ans="y"
  fi

  case "$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')" in
    y|yes)
      if [[ -e "$REPO_ROOT/$SHIP_SUBDIR" ]]; then
        echo "Error: path already exists: $REPO_ROOT/$SHIP_SUBDIR — remove it or set SHIP_SUBDIR." >&2
        exit 1
      fi
      git submodule add -b "$SHIP_BRANCH" "$SHIP_REPO_URL" "$SHIP_SUBDIR"
      git submodule update --init --recursive "$SHIP_SUBDIR"
      ;;
    *)
      echo "Add Ship (submodule or vendored copy) so this file exists, then re-run:" >&2
      echo "  $playbook_generic" >&2
      exit 1
      ;;
  esac

  if [[ ! -f "$playbook_generic" ]]; then
    echo "Error: playbook still missing after submodule add: $playbook_generic" >&2
    exit 1
  fi
}

prompt_agent() {
  if [[ -n "${SHIP_AGENT:-}" ]]; then
    case "$(printf '%s' "$SHIP_AGENT" | tr '[:upper:]' '[:lower:]')" in
      cursor|1) echo "cursor" ;;
      claude|claude-code|2) echo "claude" ;;
      *)
        echo "Error: SHIP_AGENT must be cursor or claude (got: $SHIP_AGENT)" >&2
        exit 1
        ;;
    esac
    return 0
  fi

  echo "" >&2
  echo "=== Ship adoption — choose your primary coding agent ===" >&2
  echo "  1) Cursor (IDE — Composer / Agent)" >&2
  echo "  2) Claude Code (terminal CLI)" >&2
  echo "" >&2
  local pick=""
  while [[ ! "$pick" =~ ^[12]$ ]]; do
    read -r -p "Enter 1 or 2: " pick || exit 1
  done
  if [[ "$pick" == "1" ]]; then
    echo "cursor"
  else
    echo "claude"
  fi
}

prompt_elmundi() {
  if [[ -n "${SHIP_ELMUNDI:-}" ]]; then
    if [[ "$SHIP_ELMUNDI" == "1" ]]; then echo "yes"; else echo "no"; fi
    return 0
  fi
  if [[ -n "${SHIP_NONINTERACTIVE:-}" ]]; then
    echo "no"
    return 0
  fi
  local ans=""
  read -r -p "Is this the ElMundi monorepo? (also apply adopt-ship-elmundi.md) [y/N] " ans || true
  ans="$(printf '%s' "${ans:-n}" | tr '[:upper:]' '[:lower:]')"
  if [[ "$ans" == "y" || "$ans" == "yes" ]]; then
    echo "yes"
  else
    echo "no"
  fi
}

copy_hint() {
  local text="$1"
  if command -v pbcopy >/dev/null 2>&1; then
    printf '%s' "$text" | pbcopy
    echo "(Copied suggested instruction to clipboard.)"
  elif command -v xclip >/dev/null 2>&1; then
    printf '%s' "$text" | xclip -selection clipboard 2>/dev/null && echo "(Copied suggested instruction to clipboard.)" || true
  fi
}

run_cursor() {
  local elmundi="$1"
  local at_path="@${SHIP_SUBDIR}/prompts/onboarding/adopt-ship-generic.md"
  local at_elmundi="@${SHIP_SUBDIR}/prompts/onboarding/adopt-ship-elmundi.md"

  echo ""
  echo "=== Cursor ==="
  echo "1. Open this repo in Cursor (File → Open Folder → $REPO_ROOT)."
  if command -v cursor >/dev/null 2>&1; then
    echo "   Launching: cursor \"$REPO_ROOT\""
    cursor "$REPO_ROOT" >/dev/null 2>&1 || true
  fi
  echo "2. In Composer or Agent, attach: $at_path"
  if [[ "$elmundi" == "yes" ]]; then
    echo "   Also attach: $at_elmundi"
  fi
  echo "3. Say: Execute this playbook. Create branch chore/ship-adopt. Open a PR when done."
  echo ""

  local clip="Execute the attached Ship adoption playbook. Create branch chore/ship-adopt. Open a PR when done."
  if [[ "$elmundi" == "yes" ]]; then
    clip="$clip After generic is done, also execute adopt-ship-elmundi.md (already in context if attached)."
  fi
  copy_hint "$clip"
}

run_claude() {
  local elmundi="$1"
  if ! command -v claude >/dev/null 2>&1; then
    echo "Error: Claude Code CLI not found (expected 'claude' on PATH)." >&2
    echo "Install Claude Code, then re-run this script or paste the playbook manually:" >&2
    echo "  $playbook_generic" >&2
    exit 1
  fi

  local msg
  msg="You are integrating Ship into the current repository.

Repository root: $REPO_ROOT

Read and execute the playbook file exactly (all sections, definition of done):
  $playbook_generic

Create git branch chore/ship-adopt (reuse if it already exists and is appropriate). Open a PR when finished; use the PR body template from the playbook."

  if [[ "$elmundi" == "yes" ]]; then
    msg="$msg

After the generic playbook is satisfied, read and apply the ElMundi addendum:
  $playbook_elmundi"
  fi

  echo ""
  echo "=== Claude Code ==="
  echo "Starting: claude (with adoption prompt)…"
  exec claude "$msg"
}

ensure_submodule
AGENT="$(prompt_agent)"
ELMUNDI="$(prompt_elmundi)"

if [[ "$AGENT" == "cursor" ]]; then
  run_cursor "$ELMUNDI"
else
  run_claude "$ELMUNDI"
fi
