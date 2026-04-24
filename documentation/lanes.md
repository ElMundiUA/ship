# Lanes — renamed to Automations

This page has moved to **[Automations](./automations.md)**.

The operator-facing surface that used to be called *Lanes* is now called **Automations**, matching the new IA defined in [RFC-0010](./protocol/rfc-0010-plays-and-inbox.md). The `lanes:` key in `.ship/config.yml` is unchanged — only the operator-facing name has shifted. If you're looking for:

- The `/automations` console page, the Coverage tab, the assignment wizard, the `shipctl lanes install` flow → **[Automations](./automations.md)**.
- The field-by-field YAML schema for `lanes:` → **[Configuration → `lanes`](./configuration.md#lanes)**.
- The operator vocabulary (Play, Automation, Run, Inbox, Coverage, Navigator) → **[Concepts](./concepts.md)**.
- The normative IA spec → **[RFC-0010](./protocol/rfc-0010-plays-and-inbox.md)**.
- The normative execution model behind `lanes:` → **[RFC-0007](./protocol/rfc-0007-lanes-and-run-agent.md)**.
