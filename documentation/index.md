# Ship manual

<section class="ship-hero" markdown="1">
  <p class="ship-kicker">Instruction-first SDLC framework</p>
  <h1>One method, your stack — no vendor lock-in</h1>
  <p>
    Ship is a portable methodology for shipping with agents. One CLI
    (<code>shipctl</code>), one config (<code>.ship/config.yml</code>), and a small
    set of artifacts that bind the methodology to whatever tracker, CI, agent
    runtime, and language you already use.
  </p>
  <div class="ship-hero-actions">
    <a class="md-button md-button--primary" href="getting-started/">Build your init command</a>
    <a class="md-button" href="/cli">CLI reference</a>
    <a class="md-button" href="/book">Read the book</a>
  </div>
</section>

## What this manual covers

The manual is the *operating* surface — how to adopt Ship in your repo, what
each command does, and what every artifact promises. The catalogue surfaces —
[use cases](/use-cases), [org patterns](/patterns), [workflows](/workflows),
[tools](/tools), and [collections](/collections) — live at the top of the
site so they stay one click away from the landing page; this manual links into
them when relevant.

<div class="ship-card-grid" markdown="1">
  <a class="ship-card" href="getting-started/">
    <h3>Getting started</h3>
    <p>Form-driven wizard that emits the exact <code>shipctl init</code> command and the agent prompt for your stack.</p>
  </a>
  <a class="ship-card" href="adoption/">
    <h3>Adoption hub</h3>
    <p>Three adoption paths, the agent setup contract, the launch matrix, and the delivery / quality / release model.</p>
  </a>
  <a class="ship-card" href="prompts-workflows/">
    <h3>Prompts &amp; workflows</h3>
    <p>How prompt text evolves like config code: skeletons, the ElMundi state flow, and the cloud-agent role catalog.</p>
  </a>
  <a class="ship-card" href="rfc/">
    <h3>Protocol RFCs</h3>
    <p>Authoritative spec for artifacts, <code>.ship/config.yml</code>, telemetry, adapters, and the on-disk folder layout.</p>
  </a>
  <a class="ship-card" href="examples/elmundi/">
    <h3>Reference deployment</h3>
    <p>How ElMundi wires the manual end-to-end: Linear projects, Actions cron grid, Cursor Cloud, Playwright, Sentry.</p>
  </a>
  <a class="ship-card" href="/cli">
    <h3>CLI reference</h3>
    <p><code>shipctl init / doctor / sync / verify / new</code> — every command, every flag, every check.</p>
  </a>
</div>

## Outcomes Ship is designed for

- **Onboarding is a contract, not a tribal call.** The agent runs an
  interactive setup before touching files; humans confirm the inferred stack.
- **Portable interfaces.** Tracker, CI, secret store, and agent runtime are
  adapters (RFC-0004); none of them is hard-coded into the methodology.
- **Auditable evidence.** Every automated step leaves
  <code>&lt;kind&gt;:&lt;id&gt;@&lt;version&gt;</code> in the PR, a comment in the
  ticket, and a workflow-run URL — by construction, not by promise.
- **No vendoring.** Methodology bodies live on the Ship site; clients cache
  via <code>shipctl sync</code> and never fork the rule files.

## Buying &amp; procurement {#buying-and-procurement}

Ship is methodology + prompts + reference implementations. There is no
mandatory vendor bundle, no implicit SLA unless your org adds one, and the
exit path is explicit because every interface is documented in an RFC.

## Documentation versioning {#documentation-versioning}

Current manual version: **0.10.0** — shipped with this repository. The
manual moves with the code; if a doc and an RFC disagree, the RFC wins.

## License

Apache 2.0 for this repository unless a file says otherwise. See
[Legal &amp; copyright](legal-copyright.md).
