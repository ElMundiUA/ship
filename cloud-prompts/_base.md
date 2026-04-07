## Global rules (Cursor Cloud Agent — GitHub SDLC)

- **SDLC queue:** GitHub pick scripts only take tickets in **Todo** and in the Linear project configured by **`LINEAR_SDLC_PROJECT_ID`** or **`LINEAR_SDLC_PROJECT_NAME`**. **Backlog** is for manual triage — automation does not pick it up until you move the card to Todo.
- **Linear:** The single channel with humans is **comments on the ticket** in Linear. Do not spam: one substantive comment per pass; end with `[GitHub SDLC:{{ROLE}}]`.
- **IDEMPOTENCY:** Before making changes, re-read the ticket (and latest comments). If work for this role is already done (required labels/description/status), **exit without changes and without a comment**.
- **GitHub schedule:** The same ticket can re-enter pick after ~2h. If the latest comment with `[GitHub SDLC:{{ROLE}}]` already reflects the current state and there are no new inputs — **do not duplicate** updates or comments.
- Do not merge PRs. Do not move to Done without explicit human approval.
- **LINEAR_API_KEY:** Must be available to the agent (Cursor Cloud → Secrets / env for the repository). Update Linear via API or the official client. If the key is missing — one comment `[LINEAR-DRAFT]` with JSON/text of what to apply manually.
- **Skills:** Context from `.cursor/skills` appears below — follow it for Bunny, deploy, self-heal, etc., when the task touches those areas.
- **One ticket — one open PR from SDLC:** branch **`fix/{{ISSUE}}-auto`** (issue key from the ticket, e.g. `ENG-12` → `fix/ENG-12-auto`; see `cloud-agent-launch.mjs`). Do not create a parallel **`feature/…-auto`** branch for the same ticket — that duplicates work; if two PRs already exist, do not merge both: keep the current one and close the other.
- **Dev / prod / deploy:** org-specific hosts, runbooks, and workflow names live in **repository settings** and **docs Examples** (reference deployment). Follow your team’s documented pre-release and promote process — do not assume a particular domain or image name.
- **Daily audit roles** (`tech-architect`, `qa-architect`, `security-officer`): do not create tickets without verifiable facts; dedupe against open issues in target Linear projects. Comment marker: `[GitHub SDLC daily-audit:…]` (see `docs/DAILY-AUDIT-ROLES.md` when present).

## Relevant skills

{{SKILLS_CONTEXT}}
