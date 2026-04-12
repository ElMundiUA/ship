# Getting started — ready-to-go

This is the operational entrypoint.

If you are a beginner, fill the quick form below and copy the generated prompt to your agent.

## 1) Build and copy your agent prompt

<div class="ship-form-card" markdown="1">
<div class="ship-form-grid">
  <div class="ship-field">
    <label for="tracker"><strong>Tracker</strong></label>
    <select id="tracker">
      <option>Linear</option>
      <option>Jira</option>
      <option>GitHub Issues</option>
      <option>Azure DevOps</option>
      <option>ClickUp</option>
      <option>Spreadsheet / Airtable / Notion</option>
      <option>Other</option>
    </select>
    <input id="tracker-other" class="ship-other" placeholder="Custom tracker" />
  </div>

  <div class="ship-field">
    <label for="scheduler"><strong>Scheduler / CI</strong></label>
    <select id="scheduler">
      <option>GitHub Actions</option>
      <option>GitLab CI</option>
      <option>Buildkite</option>
      <option>CircleCI</option>
      <option>Manual / no scheduler yet</option>
      <option>Other</option>
    </select>
    <input id="scheduler-other" class="ship-other" placeholder="Custom scheduler/CI" />
  </div>

  <div class="ship-field">
    <label for="agent"><strong>Agent runtime</strong></label>
    <select id="agent">
      <option>Cursor</option>
      <option>Codex</option>
      <option>Claude Code</option>
      <option>GitHub Copilot</option>
      <option>Other</option>
    </select>
    <input id="agent-other" class="ship-other" placeholder="Custom agent runtime" />
  </div>

  <div class="ship-field">
    <label for="release"><strong>Release policy</strong></label>
    <select id="release">
      <option>Manual promotion to prod</option>
      <option>Scheduled promotion with gates</option>
      <option>Hybrid (manual approval + schedule)</option>
      <option>Other</option>
    </select>
    <input id="release-other" class="ship-other" placeholder="Custom release policy" />
  </div>

  <div class="ship-field ship-field-full">
    <label for="emails"><strong>Daily emails</strong></label>
    <select id="emails">
      <option>Same DL for digest and retro</option>
      <option>Separate DLs (digest / retro)</option>
      <option>Not configured yet</option>
    </select>
  </div>
</div>

<div class="ship-actions">
  <button type="button" class="md-button md-button--primary" onclick="shipBuildPrompt()">Regenerate prompt</button>
  <button type="button" class="md-button" onclick="shipCopyPrompt()">Copy prompt</button>
</div>

<textarea id="ship-generated-prompt" rows="16" class="ship-prompt-output"></textarea>
</div>

=== "Helper command (optional)"

    From product repo root:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/ElMundiUA/ship/main/adopt-ship.sh | bash
    ```

<script>
  function shipToggleOther(selectId, inputId) {
    const sel = document.getElementById(selectId);
    const inp = document.getElementById(inputId);
    if (!sel || !inp) return;
    inp.style.display = sel.value === "Other" ? "block" : "none";
  }

  function shipPickValue(selectId, inputId) {
    const sel = document.getElementById(selectId);
    const inp = document.getElementById(inputId);
    if (!sel) return "";
    if (sel.value === "Other" && inp && inp.value.trim()) return inp.value.trim();
    return sel.value;
  }

  function shipBuildPrompt() {
    const tracker = shipPickValue("tracker", "tracker-other");
    const scheduler = shipPickValue("scheduler", "scheduler-other");
    const agent = shipPickValue("agent", "agent-other");
    const release = shipPickValue("release", "release-other");
    const emails = document.getElementById("emails")?.value || "Not configured yet";

    const prompt = `You are integrating Ship into THIS repository.

Known context from user:
- Tracker: ${tracker}
- Scheduler/CI: ${scheduler}
- Agent runtime: ${agent}
- Release policy: ${release}
- Daily emails setup: ${emails}

First, run an interactive discovery interview to validate/complete assumptions.
Then follow:
1) SHIP_ROOT/prompts/onboarding/adopt-ship-generic.md
2) SHIP_ROOT/documentation/adoption/agent-setup-contract.md
3) SHIP_ROOT/documentation/adoption/delivery-quality-and-release-process.md

Deliver one PR with adoption notes (mapping, workflows, gates, secrets names, follow-ups).
Never commit secrets.`;

    const out = document.getElementById("ship-generated-prompt");
    if (out) out.value = prompt;
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

  ["tracker", "scheduler", "agent", "release"].forEach((id) => {
    const inputMap = { tracker: "tracker-other", scheduler: "scheduler-other", agent: "agent-other", release: "release-other" };
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => shipToggleOther(id, inputMap[id]));
    shipToggleOther(id, inputMap[id]);
  });
  shipBuildPrompt();
</script>

## 2) Minimal human checklist

- [ ] Decide where Ship is present (`tools/ship` submodule or vendored copy).
- [ ] Confirm who approves production promotion.
- [ ] Confirm daily digest + daily retro recipients (recommend DL aliases).
- [ ] Add secrets/variables after the agent prepares the config list.

## 3) What Ship expects from any stack

- A queue state equivalent to `Todo`.
- Execution states equivalent to `In Progress`, `In Review`, `Done`, `Blocked`.
- A way to store routing signals (`ready:*`, `stage:*`, `result:*`) or equivalent fields.
- A place to store evidence (comments, links, reports).

## 4) Where to go next

- Interactive contract: [Agent setup contract](../adoption/agent-setup-contract.md)
- Process policy: [Delivery, quality & release](../adoption/delivery-quality-and-release-process.md)
- Tracker mapping: [Tracker adaptation contract](../tools/ship-agent-trackers.md)
- Long rationale: [The book](../framework/index.md)
- Reference implementation: [ElMundi](../examples/elmundi/index.md)
