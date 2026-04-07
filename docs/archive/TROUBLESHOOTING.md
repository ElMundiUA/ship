# Troubleshooting

**Purpose:** symptom → where to look → fix.  
**Audience:** operators with GitHub + Linear access.

| Symptom | Check | Action |
|--------|--------|--------|
| **Pick issue** fails with `MISSING_LINEAR_API_KEY` | GitHub → Secrets → `LINEAR_API_KEY` | Add repo/org secret; redeploy workflow |
| **Cloud Agent** does not start | `CURSOR_API_KEY` in GitHub secrets; Cursor dashboard allows repo | See [Cursor Cloud secrets](CLOUD-AGENT-SECRETS.md) |
| **Green SDLC run** but ticket still **Todo** | Wrong workflow state id for team was a past failure mode; verify with CLI | `node dist/cli.js start --issue ELM-XX --role developer` — expect **In Progress** |
| **Duplicate PRs** for same issue (`feature/…-auto` vs `fix/…-auto`) | Agent targets `fix/ELM-XX-auto` | Close extra PR without merge; keep `fix/…` per [Pre-release & E2E](PRE-RELEASE-DEPLOY-E2E.md) |
| **401** on Bunny API key exchange | Using wrong key type | Use exchange flow with **`BUNNY_MAIN_API_KEY`** as in Pre-release doc |
| **Snyk / security** audit skipped | Missing `SNYK_TOKEN` | Expected: job logs skip; add token for full run ([Daily audits](DAILY-AUDIT-ROLES.md)) |
| **Queues unclear** | Linear board vs script | `node scripts/agent-queue-snapshot.mjs` ([SDLC (scheduled)](SDLC-AUTOMATION-SETUP.md)) |
| **Setup sanity** | Local env | `bash scripts/verify-setup.sh` ([Autonomous pipeline setup](AUTONOMOUS-SETUP.md)) |

**Related:** [Workflows catalog](WORKFLOWS-CATALOG.md) · [Glossary](GLOSSARY.md).
