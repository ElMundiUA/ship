# Lanes — legacy name for routines

This page is retained for old links. The current runtime vocabulary is **routines** under `process.routines` in `.ship/config.yml`.

The old `lanes:` key and `shipctl lanes` command are compatibility surfaces for already-seeded repositories. New examples and new seed bundles should use `process.routines` and `shipctl run --routine`.

- The current runtime model → **[Automations / routines](./automations.md)**.
- The current CLI surface → **[`shipctl run --routine`](/cli#run)**.
- Legacy wrapper reconciliation → `shipctl lanes install`.
- The normative IA spec → **[RFC-0010](./protocol/rfc-0010-plays-and-inbox.md)**.
- The original execution model behind `lanes:` → **[RFC-0007](./protocol/rfc-0007-lanes-and-run-agent.md)**.
