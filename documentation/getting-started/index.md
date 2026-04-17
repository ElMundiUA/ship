# Getting started — ready-to-go

This is the operational entrypoint. Everything Ship does in your repo runs
through one CLI binary — **`shipctl`** — and one config file —
**`.ship/config.yml`**. There are three adoption paths; pick the one that
matches the repo you are sitting in, copy the command, and you are done.

If you are a beginner, fill the form below to generate the exact
`shipctl init` command and a short prompt for whichever agent you use.

## 1) Build your `shipctl init` command

<div class="ship-form-card" markdown="1">
<div class="ship-form-grid">
  <div class="ship-field">
    <label for="tracker"><strong>Tracker (RFC-0002)</strong></label>
    <select id="tracker">
      <option value="linear">linear</option>
      <option value="jira">jira</option>
      <option value="github-issues">github-issues</option>
      <option value="azure-boards">azure-boards</option>
      <option value="clickup">clickup</option>
      <option value="spreadsheet">spreadsheet</option>
      <option value="none">none</option>
    </select>
  </div>

  <div class="ship-field">
    <label for="ci"><strong>CI (RFC-0002)</strong></label>
    <select id="ci">
      <option value="gh-actions">gh-actions</option>
      <option value="gitlab-ci">gitlab-ci</option>
      <option value="buildkite">buildkite</option>
      <option value="circleci">circleci</option>
      <option value="azure-pipelines">azure-pipelines</option>
      <option value="jenkins">jenkins</option>
      <option value="manual">manual</option>
    </select>
  </div>

  <div class="ship-field">
    <label for="preset"><strong>Preset (RFC-0004)</strong></label>
    <select id="preset">
      <option value="web-app">web-app</option>
      <option value="api-backend">api-backend</option>
      <option value="mobile-app">mobile-app</option>
      <option value="cli">cli</option>
      <option value="monorepo">monorepo</option>
      <option value="adoption-minimum">adoption-minimum</option>
    </select>
  </div>

  <div class="ship-field ship-field-full">
    <label><strong>Agents</strong> (multi-select)</label>
    <div id="agents-checkboxes" class="ship-checkbox-grid">
      <label><input type="checkbox" class="ship-agent" value="cursor" checked /> cursor</label>
      <label><input type="checkbox" class="ship-agent" value="codex" /> codex</label>
      <label><input type="checkbox" class="ship-agent" value="claude-md" /> claude-md</label>
      <label><input type="checkbox" class="ship-agent" value="claude" /> claude</label>
      <label><input type="checkbox" class="ship-agent" value="copilot" /> copilot</label>
      <label><input type="checkbox" class="ship-agent" value="aider" /> aider</label>
      <label><input type="checkbox" class="ship-agent" value="cline" /> cline</label>
      <label><input type="checkbox" class="ship-agent" value="continue" /> continue</label>
      <label><input type="checkbox" class="ship-agent" value="windsurf" /> windsurf</label>
      <label><input type="checkbox" class="ship-agent" value="zed" /> zed</label>
      <label><input type="checkbox" class="ship-agent" value="gemini" /> gemini</label>
      <label><input type="checkbox" class="ship-agent" value="opencode" /> opencode</label>
      <label><input type="checkbox" class="ship-agent" value="cursor-cloud" /> cursor-cloud</label>
    </div>
  </div>

  <div class="ship-field">
    <label for="mode"><strong>Adoption path</strong></label>
    <select id="mode">
      <option value="existing">Existing repo (init)</option>
      <option value="new">Greenfield (new)</option>
      <option value="verify">Verify only (verify --no-network)</option>
    </select>
  </div>

  <div class="ship-field">
    <label for="project-name"><strong>Project name</strong> (greenfield only)</label>
    <input id="project-name" type="text" placeholder="my-product" />
  </div>
</div>

<div class="ship-actions">
  <button type="button" class="md-button md-button--primary" onclick="shipBuildPrompt()">Regenerate command + prompt</button>
  <button type="button" class="md-button" onclick="shipCopyCommand()">Copy command</button>
  <button type="button" class="md-button" onclick="shipCopyPrompt()">Copy agent prompt</button>
</div>

<label for="ship-generated-command"><strong>Command (paste in your repo)</strong></label>
<textarea id="ship-generated-command" rows="3" class="ship-prompt-output"></textarea>

<label for="ship-generated-prompt"><strong>Agent prompt (paste in Cursor / Codex / Claude)</strong></label>
<textarea id="ship-generated-prompt" rows="14" class="ship-prompt-output"></textarea>
</div>

## 2) Three adoption paths

=== "Existing repo"

    Most teams. Run from the root of the repo you want agents to operate on:

    ```bash
    npx @elmundi/ship-cli init --yes \
      --agents cursor,codex,claude-md \
      --tracker linear --ci gh-actions --preset web-app \
      --copy-rules
    ```

    `shipctl init` writes `.ship/config.yml` (RFC-0002), seeds the cache,
    installs the per-agent rule files at the install targets declared in each
    `collection/agent-rules-<agent>` artifact, and stops short of CI/tracker
    scaffolding. Add `--bootstrap` for the supported `mobile-app + gh-actions
    + linear` skeleton (others get a `SHIP_BOOTSTRAP_PLAN.md`).

=== "Greenfield"

    Empty directory, brand-new product:

    ```bash
    npx @elmundi/ship-cli new my-product \
      --preset web-app --tracker linear --ci gh-actions \
      --agents cursor,codex --yes
    cd my-product
    ```

    `shipctl new` runs `git init`, drops a minimal `README.md`, seeds
    `.ship/config.yml`, and runs `init --copy-rules` for the listed agents.
    Use `--here` to scaffold into the current directory instead of creating
    `<name>/`.

=== "Quick verify"

    Anywhere with a `.ship/config.yml` already in place — useful in CI or as
    a smoke test after a sync:

    ```bash
    npx @elmundi/ship-cli verify --no-network
    ```

    Runs every check under `cli/lib/verify/checks/`: config schema, gitignore,
    rules markers, cache integrity, bootstrap markers, declared-agent disk
    signals. `--no-network` skips the manifest / Linear / secret reachability
    probes for offline runs.

## 3) What Ship expects from any stack

- A queue state equivalent to `Todo`.
- Execution states equivalent to `In Progress`, `In Review`, `Done`, `Blocked`.
- A way to store routing signals (`ready:*`, `stage:*`, `result:*`) or equivalent fields.
- A place to store evidence (comments, links, reports).
- A CI surface that can run lint → build → test → e2e → delivery → release.
- Secrets handled by the platform's secret store (never in `.ship/config.yml`).
- An agent — any of the 13 supported — with on-disk markers Ship can detect.
- A way to record `<kind>:<id>@<version>` per consumed artifact in the PR.
- An owner for promotion to prod (manual approver or scheduled gate).
- A digest/retro recipient (DL alias recommended, not a personal email).

## 4) After init: keep the loop tight

- Inspect: `shipctl doctor` — proposes a stack from on-disk signals; pair
  with `--write-inventory` to persist `.ship/inventory.json`.
- Stay current: `shipctl sync` — refreshes the cache; honours `artifacts.pins`.
- Verify: `shipctl verify` — full local + network checks.
- Configure: `shipctl config get|set|show` — atomic edits on `.ship/config.yml`.

## 5) Where to go next

- CLI quick reference: [shipctl CLI](../tools/shipctl-cli.md)
- Adoption hub: [Pick a path](../adoption/index.md)
- Interactive contract: [Agent setup contract](../adoption/agent-setup-contract.md)
- Process policy: [Delivery, quality & release](../adoption/delivery-quality-and-release-process.md)
- Tracker mapping: [Tracker adapters](../tools/ship-agent-trackers.md)
- CI mapping: [CI adapters](../tools/ship-agent-ci.md)
- Protocol RFCs: [RFC index](../rfc/index.md)
- Long rationale: [The book](../framework/index.md)
- Reference implementation: [ElMundi](../examples/elmundi/index.md)

<script>
  const SHIP_AGENT_DEFAULT = ["cursor"];

  function shipPickedAgents() {
    const boxes = Array.from(document.querySelectorAll(".ship-agent"));
    const picked = boxes.filter((b) => b.checked).map((b) => b.value);
    return picked.length ? picked : SHIP_AGENT_DEFAULT.slice();
  }

  function shipPickValue(id) {
    const el = document.getElementById(id);
    return el ? el.value : "";
  }

  function shipBuildPrompt() {
    const tracker = shipPickValue("tracker");
    const ci = shipPickValue("ci");
    const preset = shipPickValue("preset");
    const agents = shipPickedAgents();
    const mode = shipPickValue("mode") || "existing";
    const projectName = (shipPickValue("project-name") || "my-product").trim().replace(/\s+/g, "-");

    const agentsCsv = agents.join(",");
    let command = "";
    if (mode === "new") {
      command = `npx @elmundi/ship-cli new ${projectName} \\\n` +
        `  --preset ${preset} --tracker ${tracker} --ci ${ci} \\\n` +
        `  --agents ${agentsCsv} --yes`;
    } else if (mode === "verify") {
      command = `npx @elmundi/ship-cli verify --no-network`;
    } else {
      command = `npx @elmundi/ship-cli init --yes \\\n` +
        `  --agents ${agentsCsv} \\\n` +
        `  --tracker ${tracker} --ci ${ci} --preset ${preset} \\\n` +
        `  --copy-rules`;
    }

    const cmdOut = document.getElementById("ship-generated-command");
    if (cmdOut) cmdOut.value = command;

    const prompt = `You are integrating Ship into THIS repository.

Stack hints from the user:
- Tracker: ${tracker}
- CI: ${ci}
- Preset: ${preset}
- Agents: ${agentsCsv}

Follow the Ship artifacts protocol (RFC-0001). Every artifact you consume
MUST be resolved via \`shipctl\` and recorded in the PR description as
\`<kind>:<id>@<version>\` (one per line). Never vendor artifact bodies into
the repository.

Steps:
1. Run the command above (or its dry-run twin) and confirm \`.ship/config.yml\`
   matches RFC-0002.
2. Read the protocol you must obey via \`shipctl pattern show\` /
   \`shipctl docs fetch\`:
   - documentation/rfc/rfc-0001-artifacts-protocol.md
   - documentation/rfc/rfc-0002-shipctl-config.md
   - documentation/adoption/agent-setup-contract.md
3. Run a discovery interview with the human: tracker fields, CI stages,
   release policy, evidence trail, secret names. Record each answer.
4. Resolve and apply the relevant rule + preset collections via \`shipctl\`
   (\`collection/agent-rules-<your-id>\`, \`collection/preset-${preset}\`).
   Install marker-delimited content at the targets in each artifact's
   front-matter.
5. Open one PR with the adoption notes (mapping, gates, secret names,
   follow-ups) and the \`<kind>:<id>@<version>\` list of consumed artifacts.

Day two:
- \`shipctl doctor\` — refresh stack inference.
- \`shipctl sync\` — pull artifact updates (honours \`artifacts.pins\`).
- \`shipctl verify\` — local + network checks before merging.

Never commit secrets. Never mutate \`.ship/config.yml\` outside
\`shipctl config\` calls.`;

    const out = document.getElementById("ship-generated-prompt");
    if (out) out.value = prompt;
  }

  function shipCopyCommand() {
    const out = document.getElementById("ship-generated-command");
    if (!out) return;
    out.select();
    out.setSelectionRange(0, 99999);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(out.value);
      return;
    }
    document.execCommand("copy");
  }

  function shipCopyPrompt() {
    const out = document.getElementById("ship-generated-prompt");
    if (!out) return;
    out.select();
    out.setSelectionRange(0, 99999);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(out.value);
      return;
    }
    document.execCommand("copy");
  }

  ["tracker", "ci", "preset", "mode", "project-name"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", shipBuildPrompt);
    if (el) el.addEventListener("input", shipBuildPrompt);
  });
  document.querySelectorAll(".ship-agent").forEach((box) => {
    box.addEventListener("change", shipBuildPrompt);
  });
  shipBuildPrompt();
</script>
