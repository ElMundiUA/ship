# Agent playbook (canonical)

The block below is **included from** the repository file `prompts/onboarding/adopt-ship-generic.md` so the published manual and the git-tracked prompt stay identical.

The canonical entrypoint is the `shipctl` CLI. After the discovery interview
the agent runs:

```bash
npx @elmundi/ship-cli init --yes \
  --agents <csv> \
  --tracker <t> --ci <c> --preset <p> \
  --copy-rules
```

Add `--bootstrap` only after the human confirms the chosen preset triple is
supported (today: `mobile-app + gh-actions + linear`); other combos emit a
`SHIP_BOOTSTRAP_PLAN.md` checklist instead. See
[shipctl CLI reference](../tools/shipctl-cli.md) for every flag and
[Agent setup contract](agent-setup-contract.md) for the discovery interview
the playbook below assumes.

--8<-- "prompts/onboarding/adopt-ship-generic.md"
