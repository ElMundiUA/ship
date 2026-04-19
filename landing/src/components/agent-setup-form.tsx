"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { repoUrl } from "@/lib/config";

const PRESETS = [
  "web-app",
  "api-backend",
  "mobile-app",
  "cli",
  "monorepo",
  "adoption-minimum",
] as const;
const TRACKERS = [
  "linear",
  "jira",
  "github-issues",
  "azure-boards",
  "clickup",
  "spreadsheet",
  "none",
] as const;
const CIS = [
  "gh-actions",
  "gitlab-ci",
  "buildkite",
  "circleci",
  "azure-pipelines",
  "jenkins",
  "manual",
] as const;
const AGENTS: { id: string; label: string }[] = [
  { id: "cursor", label: "cursor" },
  { id: "codex", label: "codex" },
  { id: "claude", label: "claude" },
  { id: "claude-md", label: "claude-md" },
  { id: "agents-md", label: "agents-md" },
  { id: "copilot", label: "copilot" },
  { id: "aider", label: "aider" },
  { id: "cline", label: "cline" },
  { id: "continue", label: "continue" },
  { id: "windsurf", label: "windsurf" },
  { id: "zed", label: "zed" },
  { id: "gemini", label: "gemini" },
  { id: "opencode", label: "opencode" },
  { id: "cursor-cloud", label: "cursor-cloud" },
];
const LANGUAGES = ["", "ts", "js", "py", "go", "rust", "java", "kotlin", "swift", "dart", "multi"] as const;
const CHANNELS = ["stable", "edge"] as const;
const TELEMETRY = ["off", "on"] as const;

type Mode = "new" | "init" | "verify";

type PharmaPreset = {
  mode: Mode;
  projectName: string;
  preset: (typeof PRESETS)[number];
  tracker: (typeof TRACKERS)[number];
  ci: (typeof CIS)[number];
  agents: string[];
  language: (typeof LANGUAGES)[number];
  channel: (typeof CHANNELS)[number];
  telemetry: (typeof TELEMETRY)[number];
};

const PHARMA_PILOT: PharmaPreset = {
  mode: "new",
  projectName: "pharma-pilot",
  preset: "mobile-app",
  tracker: "linear",
  ci: "gh-actions",
  agents: ["cursor", "claude-md", "codex"],
  language: "ts",
  channel: "stable",
  telemetry: "off",
};

const BRANCH = "main";
const PHARMA_PRESET_URL = `${repoUrl}/blob/${BRANCH}/artifacts/collections/preset-mobile-app/ARTIFACT.md`;
const PHARMA_ADDENDUM_URL = `${repoUrl}/blob/${BRANCH}/artifacts/collections/addendum-pharma/ARTIFACT.md`;

function buildCommand(s: {
  mode: Mode;
  projectName: string;
  preset: string;
  tracker: string;
  ci: string;
  agents: string[];
  language: string;
  channel: string;
  telemetry: string;
}): string {
  const agents = s.agents.length ? s.agents.join(",") : "cursor";
  const langFlag = s.language ? ` --language ${s.language}` : "";
  const channelFlag = s.channel && s.channel !== "stable" ? ` --channel ${s.channel}` : "";
  const telemetryFlag = s.telemetry === "on" ? " --telemetry on" : "";
  const name = (s.projectName || "my-product").trim().replace(/\s+/g, "-") || "my-product";

  if (s.mode === "verify") {
    return `npx @elmundi/ship-cli verify --no-network`;
  }

  if (s.mode === "new") {
    return [
      `npx @elmundi/ship-cli new ${name} \\`,
      `  --preset ${s.preset} --tracker ${s.tracker} --ci ${s.ci} \\`,
      `  --agents ${agents}${langFlag}${channelFlag}${telemetryFlag} --yes`,
    ].join("\n");
  }

  return [
    `npx @elmundi/ship-cli init --yes \\`,
    `  --agents ${agents} \\`,
    `  --tracker ${s.tracker} --ci ${s.ci} --preset ${s.preset}${langFlag}${channelFlag}${telemetryFlag} \\`,
    `  --copy-rules`,
  ].join("\n");
}

function buildPrompt(s: {
  mode: Mode;
  preset: string;
  tracker: string;
  ci: string;
  agents: string[];
  language: string;
}) {
  const agentsCsv = s.agents.length ? s.agents.join(",") : "cursor";
  return `You are integrating Ship into THIS repository following the artifacts protocol (RFC-0001).

Stack hints from the user:
- Adoption mode: ${s.mode}
- Tracker: ${s.tracker}
- CI: ${s.ci}
- Preset: ${s.preset}
- Agents: ${agentsCsv}
- Language: ${s.language || "unspecified"}

Rules:
1. Every artifact you consume MUST be resolved via \`shipctl\` (pattern / tool /
   workflow / collection show|fetch) and pinned by version. Never vendor the
   artifact body into this repository.
2. Record every consumed artifact in the PR description as one line per entry
   using \`<kind>:<id>@<version>\` (RFC-0001).
3. \`.ship/config.yml\` is the only source of truth for adapter selection
   (RFC-0002). Mutate it only via \`shipctl config set\`.
4. Telemetry is opt-in; never enable it without explicit user consent.

Steps:
1. Run the shipctl command the user copied. Confirm \`.ship/config.yml\` and the
   seeded \`.ship/cache/\` match the preset.
2. Read the protocol you must obey:
   - documentation/protocol/rfc-0001-artifacts-protocol.md
   - documentation/protocol/rfc-0002-shipctl-config.md
   - documentation/adoption/agent-setup-contract.md
3. Run a discovery interview: tracker fields, CI stages, release policy,
   evidence trail, secret *names* (never values). Persist answers in the PR.
4. Resolve and apply the relevant rule + preset collections via \`shipctl\`:
   \`collection/agent-rules-<your-id>\`, \`collection/preset-${s.preset}\`.
   Install marker-delimited content at the targets each artifact declares.
5. Open one PR with the adoption notes (mapping, gates, secret names,
   follow-ups) and the \`<kind>:<id>@<version>\` list of artifacts consumed.

Day two:
- \`shipctl doctor\` — refresh stack inference from on-disk signals.
- \`shipctl sync\` — pull artifact updates (honours \`artifacts.pins\`).
- \`shipctl verify\` — local + network checks before merging.
- \`shipctl feedback\` — capture a short report when you hit a wall.

Never commit secrets. Never mutate \`.ship/config.yml\` outside
\`shipctl config\` calls.`;
}

export function AgentSetupForm() {
  const [mode, setMode] = useState<Mode>("new");
  const [projectName, setProjectName] = useState("pharma-pilot");
  const [preset, setPreset] = useState<(typeof PRESETS)[number]>("mobile-app");
  const [tracker, setTracker] = useState<(typeof TRACKERS)[number]>("linear");
  const [ci, setCi] = useState<(typeof CIS)[number]>("gh-actions");
  const [agents, setAgents] = useState<string[]>(["cursor", "claude-md", "codex"]);
  const [language, setLanguage] = useState<(typeof LANGUAGES)[number]>("ts");
  const [channel, setChannel] = useState<(typeof CHANNELS)[number]>("stable");
  const [telemetry, setTelemetry] = useState<(typeof TELEMETRY)[number]>("off");

  const applyPharma = useCallback(() => {
    setMode(PHARMA_PILOT.mode);
    setProjectName(PHARMA_PILOT.projectName);
    setPreset(PHARMA_PILOT.preset);
    setTracker(PHARMA_PILOT.tracker);
    setCi(PHARMA_PILOT.ci);
    setAgents([...PHARMA_PILOT.agents]);
    setLanguage(PHARMA_PILOT.language);
    setChannel(PHARMA_PILOT.channel);
    setTelemetry(PHARMA_PILOT.telemetry);
  }, []);

  const toggleAgent = useCallback((id: string) => {
    setAgents((prev) => (prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]));
  }, []);

  const command = useMemo(
    () => buildCommand({ mode, projectName, preset, tracker, ci, agents, language, channel, telemetry }),
    [mode, projectName, preset, tracker, ci, agents, language, channel, telemetry],
  );

  const defaultPrompt = useMemo(
    () => buildPrompt({ mode, preset, tracker, ci, agents, language }),
    [mode, preset, tracker, ci, agents, language],
  );

  const [prompt, setPrompt] = useState(defaultPrompt);
  const [promptTouched, setPromptTouched] = useState(false);

  useEffect(() => {
    if (!promptTouched) setPrompt(defaultPrompt);
  }, [defaultPrompt, promptTouched]);

  const [copied, setCopied] = useState<"cmd" | "prompt" | null>(null);
  const copy = async (text: string, which: "cmd" | "prompt") => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(which);
      setTimeout(() => setCopied((c) => (c === which ? null : c)), 1800);
    } catch {
      /* ignore */
    }
  };

  const isVerify = mode === "verify";

  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4 sm:p-6">
      {/* Quick-launch presets */}
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-aqua/20 bg-aqua/[0.06] px-4 py-3">
        <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-aqua/80">
          Quick launch
        </span>
        <button
          type="button"
          onClick={applyPharma}
          className="rounded-full border border-aqua/40 bg-aqua/15 px-3 py-1 text-sm font-semibold text-aqua transition hover:bg-aqua/25"
        >
          Pharma mobile pilot (example)
        </button>
        <span className="text-xs text-white/50">
          Prefills: <code className="font-mono">preset=mobile-app</code>,{" "}
          <code className="font-mono">tracker=linear</code>, <code className="font-mono">ci=gh-actions</code>,{" "}
          <code className="font-mono">agents=cursor,claude-md,codex</code>.
        </span>
      </div>

      {/* Mode toggle */}
      <div className="mt-5 flex flex-wrap gap-1 rounded-xl border border-white/10 bg-black/40 p-1">
        {([
          { k: "new", label: "new  (greenfield)" },
          { k: "init", label: "init  (existing repo)" },
          { k: "verify", label: "verify  (smoke test)" },
        ] as const).map((opt) => (
          <button
            key={opt.k}
            type="button"
            onClick={() => setMode(opt.k as Mode)}
            className={`min-h-[44px] flex-1 rounded-lg px-4 py-2.5 text-sm font-semibold transition ${
              mode === opt.k
                ? "bg-gradient-to-r from-coral/95 via-lilac/85 to-aqua/95 text-zinc-950 shadow-md"
                : "text-white/60 hover:bg-white/[0.06] hover:text-white/90"
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Form */}
      {!isVerify ? (
        <div className="mt-6 grid gap-5 sm:grid-cols-2">
          {mode === "new" ? (
            <Field label="Project name">
              <input
                className="input-ship input-ship-wizard"
                placeholder="my-product"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
              />
            </Field>
          ) : (
            <Field label="Repo path">
              <p className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-white/55">
                Run from the root of the repo you want agents to operate on.
              </p>
            </Field>
          )}

          <Field label="Preset (RFC-0004)">
            <select
              className="input-ship input-ship-wizard"
              value={preset}
              onChange={(e) => setPreset(e.target.value as (typeof PRESETS)[number])}
            >
              {PRESETS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Tracker">
            <select
              className="input-ship input-ship-wizard"
              value={tracker}
              onChange={(e) => setTracker(e.target.value as (typeof TRACKERS)[number])}
            >
              {TRACKERS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </Field>

          <Field label="CI">
            <select
              className="input-ship input-ship-wizard"
              value={ci}
              onChange={(e) => setCi(e.target.value as (typeof CIS)[number])}
            >
              {CIS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Language (optional)">
            <select
              className="input-ship input-ship-wizard"
              value={language}
              onChange={(e) => setLanguage(e.target.value as (typeof LANGUAGES)[number])}
            >
              {LANGUAGES.map((l) => (
                <option key={l || "_blank"} value={l}>
                  {l || "(skip)"}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Channel">
            <select
              className="input-ship input-ship-wizard"
              value={channel}
              onChange={(e) => setChannel(e.target.value as (typeof CHANNELS)[number])}
            >
              {CHANNELS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Telemetry (opt-in)">
            <select
              className="input-ship input-ship-wizard"
              value={telemetry}
              onChange={(e) => setTelemetry(e.target.value as (typeof TELEMETRY)[number])}
            >
              {TELEMETRY.map((t) => (
                <option key={t} value={t}>
                  {t === "off" ? "off (default)" : "on"}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Agents (multi-select)" className="sm:col-span-2">
            <div className="grid gap-2 rounded-lg border border-white/10 bg-black/30 p-3 sm:grid-cols-3 md:grid-cols-4">
              {AGENTS.map((a) => {
                const checked = agents.includes(a.id);
                return (
                  <label
                    key={a.id}
                    className={`flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1.5 text-sm transition ${
                      checked
                        ? "border-aqua/40 bg-aqua/[0.08] text-white"
                        : "border-white/10 bg-white/[0.02] text-white/65 hover:border-white/25"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="accent-aqua"
                      checked={checked}
                      onChange={() => toggleAgent(a.id)}
                    />
                    <span className="font-mono text-[12px]">{a.label}</span>
                  </label>
                );
              })}
            </div>
          </Field>
        </div>
      ) : (
        <div className="mt-6 rounded-xl border border-white/10 bg-black/40 p-4 text-sm text-white/70">
          <p>
            <code className="rounded bg-white/10 px-1 py-0.5 font-mono text-aqua/90">shipctl verify --no-network</code>{" "}
            runs every check under <code className="font-mono text-aqua/90">cli/lib/verify/checks/</code>: config
            schema, gitignore, rule markers, cache integrity, bootstrap markers, and declared-agent on-disk signals.
            Use it in CI or as a post-sync smoke test. No flags to pick — copy the command.
          </p>
        </div>
      )}

      {/* Output command */}
      <div className="mt-7">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-bold uppercase tracking-[0.18em] text-white/50">Command</span>
          <button
            type="button"
            onClick={() => copy(command, "cmd")}
            className="rounded-full border border-white/15 bg-white/[0.04] px-3 py-1 text-xs font-semibold text-white/80 transition hover:border-aqua/40 hover:text-aqua"
          >
            {copied === "cmd" ? "Copied" : "Copy command"}
          </button>
        </div>
        <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/55 p-4 font-mono text-[13px] leading-relaxed text-aqua/95 sm:text-sm">
{command}
        </pre>
      </div>

      {/* Agent prompt */}
      <div className="mt-7">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-bold uppercase tracking-[0.18em] text-white/50">
            Agent prompt · artifacts-protocol v1
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setPrompt(defaultPrompt);
                setPromptTouched(false);
              }}
              className="rounded-full border border-white/15 bg-white/[0.04] px-3 py-1 text-xs font-semibold text-white/70 transition hover:border-white/30 hover:text-white"
            >
              Reset
            </button>
            <button
              type="button"
              onClick={() => copy(prompt, "prompt")}
              className="rounded-full border border-white/15 bg-white/[0.04] px-3 py-1 text-xs font-semibold text-white/80 transition hover:border-aqua/40 hover:text-aqua"
            >
              {copied === "prompt" ? "Copied" : "Copy prompt"}
            </button>
          </div>
        </div>
        <textarea
          className="input-ship input-ship-wizard min-h-[20rem] w-full resize-y rounded-xl border-white/10 bg-black/45 font-mono text-[13px] leading-relaxed sm:min-h-[24rem] sm:text-sm"
          value={prompt}
          onChange={(e) => {
            setPrompt(e.target.value);
            setPromptTouched(true);
          }}
          spellCheck={false}
        />
      </div>

      {/* Pharma links */}
      <p className="mt-5 text-xs text-white/50">
        Pharma mobile pilot references:{" "}
        <a className="text-aqua/85 underline-offset-2 hover:underline" href={PHARMA_PRESET_URL} target="_blank" rel="noreferrer">
          preset-mobile-app.md
        </a>{" "}
        ·{" "}
        <a className="text-aqua/85 underline-offset-2 hover:underline" href={PHARMA_ADDENDUM_URL} target="_blank" rel="noreferrer">
          addendum-pharma.md
        </a>
        . Both render on the site under <code className="rounded bg-white/10 px-1 font-mono">/docs/collections/</code> once
        published.
      </p>
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
