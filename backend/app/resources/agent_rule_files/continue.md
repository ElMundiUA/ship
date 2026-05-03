# Ship artifacts protocol — Continue

**Install target:** `.continue/ship.md`

Continue reads markdown rules from `.continue/`. Write the
marker-delimited body below into `.continue/ship.md`, then
register it in `.continue/config.yaml` so Continue surfaces
the rule as a custom command.

### `.continue/config.yaml` entry (merge under `customCommands`)

```yaml
customCommands:
  - name: ship-artifacts
    description: Follow the Ship artifacts protocol before applying an artifact.
    prompt: "{{{ ./ship.md }}}"
```

### `.continue/ship.md` contents (paste verbatim, keep the markers)

<!-- ship-cli: artifacts-protocol v1 -->

# Ship artifacts — fetch, verify, improve

Base URL (env `SHIP_API_BASE`, flag `--base-url`):
`https://ship.elmundi.com/api/methodology`.

Every Ship artifact (pattern / tool / workflow / collection / doc) is
versioned (semver + `content_sha256`). Clients **never** vendor artifact
bodies — they call `shipctl`, which hits `POST /fetch` and writes a local
`.ship/cache/` entry keyed by `<kind>/<id>@<version>`. The authoritative
contract is `documentation/protocol/rfc-0001-artifacts-protocol.md`.

## Agent protocol (follow before applying an artifact)

1. **Resolve before use.** Run `shipctl <kind> show <id>` (or `fetch`) so
   the body you act on is the current pinned version. Pins live in
   `.ship/config.yml` under `artifacts.pins`. If the cache is cold,
   `shipctl` fetches and verifies the `content_sha256`; never hand-roll a
   local copy.
2. **Record the exact version.** Put `<kind>:<id>@<version>` in the PR
   description (one per line) for every artifact you consumed. Example:
   `pattern:role-developer@1.4.2`. This line is the evidence trail used
   by retros and telemetry.
3. **Do not copy bodies into the repo.** If you need a snippet inline,
   reference the artifact id and version next to it. Never edit the
   cached body in place and never commit `.ship/cache/` contents.
4. **Feedback is opt-in.** When the artifact was unclear or wrong, draft
   via `shipctl feedback draft`; never auto-submit. Explicit user consent
   is required (see RFC-0003).

## CLI

```bash
shipctl pattern list
shipctl pattern show role-developer              # resolves latest or pin
shipctl pattern fetch role-developer --version 1.4.2
shipctl search "release gates and qa split" --top-k 8
shipctl docs fetch documentation/adoption/delivery-quality-and-release-process.md
shipctl sync                                       # reconcile .ship/cache/
```

## HTTP (curl, no CLI)

```bash
curl -sS -X POST "https://ship.elmundi.com/api/methodology/fetch" \
  -H "Content-Type: application/json" \
  -d '{"kind":"pattern","id":"role-developer"}'
curl -sS "https://ship.elmundi.com/api/methodology/manifest"
```

## End-of-session rules

- Before closing a PR or task, confirm that every artifact you actually
  consumed is listed in the PR description with an exact `@<version>`.
- If any artifact version changed mid-session (e.g. a `shipctl sync`),
  re-record the final version used; do not keep the stale id.
- If you diverged from an artifact's instructions, note the divergence in
  the PR description and open `shipctl feedback draft`. Do not silently
  patch the behaviour or copy the body locally.
- Stale entries in `.ship/cache/` are not evidence; telemetry only
  attributes outcomes to versions present in the PR/ticket body.
- Never commit `.ship/cache/` or any rendered artifact body to the repo —
  the cache is a local read-through of the server, not a source of truth.

See `documentation/protocol/rfc-0001-artifacts-protocol.md` for the full HTTP
surface, pin rules, and cache layout. `.ship/config.yml` schema is
RFC-0002.

<!-- ship-cli:end artifacts-protocol -->

---

Source of truth: `collections/agent-rules-continue` on the Ship site.
Fetched by: `shipctl sync` or `shipctl collection fetch agent-rules-continue`.
