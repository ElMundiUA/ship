"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const trackers = ["Linear", "Jira", "GitHub Issues", "Azure DevOps", "ClickUp", "Spreadsheet / Airtable / Notion", "Other"];
const schedulers = ["GitHub Actions", "GitLab CI", "Buildkite", "CircleCI", "Manual / no scheduler yet", "Other"];
const agents = ["Cursor", "Codex", "Claude Code", "GitHub Copilot", "Other"];
const releases = [
  "Manual promotion to prod",
  "Scheduled promotion with gates",
  "Hybrid (manual approval + schedule)",
  "Other",
];
const emails = ["Same DL for digest and retro", "Separate DLs (digest / retro)", "Not configured yet"];

const SHIP_REPO = "https://github.com/ElMundiUA/ship";
const SHIP_RAW = "https://raw.githubusercontent.com/ElMundiUA/ship/main";

function pick(select: string, otherVal: string) {
  if (select === "Other" && otherVal.trim()) return otherVal.trim();
  return select;
}

export function AgentSetupForm() {
  const [tab, setTab] = useState<"prompt" | "helper">("prompt");
  const [tracker, setTracker] = useState(trackers[0]);
  const [trackerOther, setTrackerOther] = useState("");
  const [scheduler, setScheduler] = useState(schedulers[0]);
  const [schedulerOther, setSchedulerOther] = useState("");
  const [agent, setAgent] = useState(agents[0]);
  const [agentOther, setAgentOther] = useState("");
  const [release, setRelease] = useState(releases[0]);
  const [releaseOther, setReleaseOther] = useState("");
  const [emailMode, setEmailMode] = useState(emails[0]);

  const buildPrompt = useCallback(() => {
    const t = pick(tracker, trackerOther);
    const s = pick(scheduler, schedulerOther);
    const a = pick(agent, agentOther);
    const r = pick(release, releaseOther);
    return `## Your mission
You are a coding agent working in the **current repository** (the user's product or platform codebase). You have **never** heard of "Ship" before — treat everything below as new instructions.

## What "Ship" is (read carefully)
**Ship** is an **Apache-2.0**, **instruction-first** methodology kit for SDLC automation. It is **not** a hosted SaaS you log into. It is a **git repository** of manuals, reusable prompts ("patterns"), and machine-readable catalogs (tools, workflows, collections) that teams copy, fork, or vendor next to their own code.

- Upstream home (clone from here): ${SHIP_REPO}
- You will use files under the clone root — below we call that **SHIP_ROOT** (absolute path on disk after clone).

## What you must deliver
Integrate Ship's **operating model** into **this** repository: discovery, mapping to the user's tracker/CI/agent stack, adoption notes, and **one pull request** with concrete file changes. **Never** commit secrets, API keys, or tokens.

---

## User context (honor in your plan)
| Topic | Value |
|-------|-------|
| Tracker | ${t} |
| Scheduler / CI | ${s} |
| Preferred agent runtime | ${a} |
| Release / promotion policy | ${r} |
| Email / digest setup | ${emailMode} |

---

## Step 0 — Figure out SHIP_ROOT (mandatory)
1. **Search** this workspace for \`cli/bin/ship.mjs\` or a root \`package.json\` whose \`scripts\` include \`"ship"\`. If present, **SHIP_ROOT** is the root of **that** tree (the Ship kit checkout). Run there:
   \`\`\`bash
   npm install
   npm run ship -- help
   \`\`\`
   If \`help\` prints usage, the CLI is installed — **skip cloning**.

2. If Ship is **not** in the workspace, **clone** it into a dedicated folder (pick one, document the path in your PR):
   \`\`\`bash
   git clone ${SHIP_REPO}.git vendor/ship
   cd vendor/ship
   npm install
   npm run ship -- help
   npm run ship -- pattern list
   \`\`\`
   Set **SHIP_ROOT** to the absolute path of \`vendor/ship\` (or the directory you chose).

3. **Sanity check** from SHIP_ROOT — all must succeed without network beyond npm/git:
   - \`npm run ship -- pattern list\` (reads \`patterns/manifest.json\`)
   - \`npm run ship -- tool list\`
   - \`npm run ship -- workflow list\`
   - \`npm run ship -- collection list\`

---

## Optional: scripted bootstrap (product repo root only)
If operators prefer a helper instead of manual clone, from **this product repository root** they may run:
\`\`\`bash
curl -fsSL ${SHIP_RAW}/adopt-ship.sh | bash
\`\`\`
If you use it, **read the script output**, do not blindly pipe secrets, and still verify SHIP_ROOT the same way as Step 0.

---

## Step 1 — Discovery interview (do this before editing)
Ask the human (or infer from the repo) short clarifying questions: repo layout, where CI lives, how tickets are named, where secrets live (GitHub Actions secrets, Vault, etc.), and whether they want Ship **vendored**, **subtree**, or **documentation-only** adoption.

---

## Step 2 — Follow Ship's own onboarding playbooks (in order)
Open these files **from SHIP_ROOT** (or via raw URLs if you cannot read the clone yet):

1. \`SHIP_ROOT/prompts/onboarding/adopt-ship-generic.md\`  
   Raw: ${SHIP_RAW}/prompts/onboarding/adopt-ship-generic.md

2. \`SHIP_ROOT/documentation/adoption/agent-setup-contract.md\`  
   Raw: ${SHIP_RAW}/documentation/adoption/agent-setup-contract.md

3. \`SHIP_ROOT/documentation/adoption/delivery-quality-and-release-process.md\`  
   Raw: ${SHIP_RAW}/documentation/adoption/delivery-quality-and-release-process.md

Execute the steps they describe, adapted to the user's **${t}** / **${s}** / **${a}** choices.

---

## Step 3 — After the first adoption PR is merged (human confirms)
From **SHIP_ROOT** (the Ship checkout where \`npm run ship\` works), run:
\`\`\`bash
npm run ship -- init
\`\`\`
That command **interactively** detects Cursor / Codex / Copilot instruction files and can append **API usage notes** for agents. **Do not** run non-interactively unless the user explicitly asked — it may write to \`AGENTS.md\`, \`.cursor\`, etc.

If they use a **remote** Ship API instead of local files only, they set \`SHIP_API_BASE\` (document the variable name in the PR; never paste real URLs with embedded secrets).

---

## Step 4 — Human-readable adoption notes (include in the same PR or a follow-up)
Summarize: SHIP_ROOT path, what was copied vs symlinked, tracker ↔ label mapping, CI touchpoints, open risks, and **exact** secret *names* (not values) the team must create.

---

## Reminders
- **Do not** invent Ship APIs — use \`npm run ship -- help\` and the markdown above.
- **Do not** commit credentials.
- Prefer **small**, reviewable commits inside one PR.
`;
  }, [agent, agentOther, emailMode, release, releaseOther, scheduler, schedulerOther, tracker, trackerOther]);

  const [prompt, setPrompt] = useState(buildPrompt);

  useEffect(() => {
    setPrompt(buildPrompt());
  }, [buildPrompt]);

  const helperSnippet = useMemo(
    () => `curl -fsSL https://raw.githubusercontent.com/ElMundiUA/ship/main/adopt-ship.sh | bash`,
    [],
  );

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-1 sm:p-1.5">
      <div className="flex flex-wrap gap-1 rounded-xl border border-white/10 bg-black/40 p-1">
        <button
          type="button"
          onClick={() => setTab("prompt")}
          className={`min-h-[44px] flex-1 rounded-lg px-4 py-2.5 text-sm font-semibold transition sm:flex-none sm:px-6 ${
            tab === "prompt"
              ? "bg-gradient-to-r from-coral/95 via-lilac/85 to-aqua/95 text-zinc-950 shadow-md"
              : "text-white/60 hover:bg-white/[0.06] hover:text-white/90"
          }`}
        >
          Agent prompt
        </button>
        <button
          type="button"
          onClick={() => setTab("helper")}
          className={`min-h-[44px] flex-1 rounded-lg px-4 py-2.5 text-sm font-semibold transition sm:flex-none sm:px-6 ${
            tab === "helper"
              ? "bg-gradient-to-r from-coral/95 via-lilac/85 to-aqua/95 text-zinc-950 shadow-md"
              : "text-white/60 hover:bg-white/[0.06] hover:text-white/90"
          }`}
        >
          Helper script
        </button>
      </div>

      {tab === "helper" ? (
        <div className="space-y-4 px-4 pb-5 pt-6 sm:px-6">
          <p className="text-sm leading-relaxed text-white/65">
            Optional one-liner from the <strong className="text-white/85">product repository root</strong> to pull Ship&apos;s
            helper. Inspect what it does before running in CI.
          </p>
          <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/50 p-4 text-xs leading-relaxed text-aqua/90 sm:text-sm">
            {helperSnippet}
          </pre>
          <button type="button" className="btn-secondary" onClick={() => copy(helperSnippet)}>
            Copy command
          </button>
        </div>
      ) : (
        <div className="px-4 pb-6 pt-6 sm:px-6">
          <p className="text-xs font-semibold uppercase tracking-wider text-white/40">Your delivery stack</p>
          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <Field label="Tracker">
              <select className="input-ship input-ship-wizard" value={tracker} onChange={(e) => setTracker(e.target.value)}>
                {trackers.map((x) => (
                  <option key={x} value={x}>
                    {x}
                  </option>
                ))}
              </select>
              {tracker === "Other" && (
                <input
                  className="input-ship input-ship-wizard mt-2"
                  placeholder="Custom tracker"
                  value={trackerOther}
                  onChange={(e) => setTrackerOther(e.target.value)}
                />
              )}
            </Field>
            <Field label="Scheduler / CI">
              <select className="input-ship input-ship-wizard" value={scheduler} onChange={(e) => setScheduler(e.target.value)}>
                {schedulers.map((x) => (
                  <option key={x} value={x}>
                    {x}
                  </option>
                ))}
              </select>
              {scheduler === "Other" && (
                <input
                  className="input-ship input-ship-wizard mt-2"
                  placeholder="Custom CI"
                  value={schedulerOther}
                  onChange={(e) => setSchedulerOther(e.target.value)}
                />
              )}
            </Field>
            <Field label="Agent runtime">
              <select className="input-ship input-ship-wizard" value={agent} onChange={(e) => setAgent(e.target.value)}>
                {agents.map((x) => (
                  <option key={x} value={x}>
                    {x}
                  </option>
                ))}
              </select>
              {agent === "Other" && (
                <input
                  className="input-ship input-ship-wizard mt-2"
                  placeholder="Custom agent"
                  value={agentOther}
                  onChange={(e) => setAgentOther(e.target.value)}
                />
              )}
            </Field>
            <Field label="Release policy">
              <select className="input-ship input-ship-wizard" value={release} onChange={(e) => setRelease(e.target.value)}>
                {releases.map((x) => (
                  <option key={x} value={x}>
                    {x}
                  </option>
                ))}
              </select>
              {release === "Other" && (
                <input
                  className="input-ship input-ship-wizard mt-2"
                  placeholder="Custom policy"
                  value={releaseOther}
                  onChange={(e) => setReleaseOther(e.target.value)}
                />
              )}
            </Field>
            <Field label="Daily emails" className="sm:col-span-2">
              <select className="input-ship input-ship-wizard" value={emailMode} onChange={(e) => setEmailMode(e.target.value)}>
                {emails.map((x) => (
                  <option key={x} value={x}>
                    {x}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="mt-6 flex flex-wrap gap-3">
            <button type="button" className="btn-primary" onClick={() => setPrompt(buildPrompt())}>
              Regenerate prompt
            </button>
            <button type="button" className="btn-secondary" onClick={() => copy(prompt)}>
              Copy prompt
            </button>
          </div>
          <label className="mt-6 block">
            <span className="mb-2 block text-xs font-semibold uppercase tracking-wider text-white/40">Generated prompt</span>
            <textarea
              className="input-ship input-ship-wizard min-h-[22rem] w-full resize-y rounded-xl border-white/10 bg-black/45 font-mono text-[13px] leading-relaxed sm:min-h-[26rem] sm:text-sm"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              spellCheck={false}
            />
          </label>
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  children,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={`flex flex-col gap-2 ${className}`}>
      <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-white/50">{label}</span>
      {children}
    </label>
  );
}
