# Agent playbook (canonical)

The canonical entrypoint is the `shipctl` CLI. After the discovery interview
(see [Agent setup contract](agent-setup-contract.md)) the agent runs:

```bash
npx @elmundi/ship-cli init --yes \
  --agents <csv> \
  --tracker <t> --ci <c> --preset <p> \
  --copy-rules
```

Add `--bootstrap` only after the human confirms the chosen preset triple is
supported (today: `mobile-app + gh-actions + linear`); other combos emit a
`SHIP_BOOTSTRAP_PLAN.md` checklist instead. The full flag surface lives in the
[shipctl CLI reference](/cli).

The body of the onboarding playbook itself is now a versioned artifact —
[`pattern/adopt-ship-generic`](/patterns/adopt-ship-generic) — resolved by
`shipctl init` via `--copy-playbook`. ElMundi-specific delta is
[`pattern/adopt-ship-elmundi`](/patterns/adopt-ship-elmundi). Edit the
artifact files (`artifacts/patterns/adopt-ship-*/ARTIFACT.md`) to change what
agents follow on day one; sync picks up the new body on the next run.
