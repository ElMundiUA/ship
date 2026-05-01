import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Roadmap — Ship",
  description:
    "What Ship ships at production depth today (Cursor, Claude Code, Codex, Copilot) and what we're growing toward — a single contract across every IDE and CLI agent, mature drift detection, cloud-side execution.",
};

type CoreAgent = {
  id: string;
  name: string;
  blurb: string;
  marker: string;
  installTarget: string;
};

type AdjacentAgent = {
  id: string;
  note: string;
};

type Agent = {
  id: string;
  marker: string;
  installTarget: string;
  adapter: string;
  invoke: string;
  initCommand: string;
  tier: "core" | "adjacent";
};

const CORE: CoreAgent[] = [
  {
    id: "cursor",
    name: "Cursor",
    blurb: "The IDE most teams in the closed beta picked up first. Mature rule body, mature install target, the path of least resistance.",
    marker: ".cursor/rules/",
    installTarget: ".cursor/rules/ship-artifacts-protocol.mdc",
  },
  {
    id: "claude",
    name: "Claude Code",
    blurb: "The CLI we test the most against. Local and SaaS variants both work; the rule body lives at the conventional CLAUDE.md root.",
    marker: "CLAUDE.md, .claude/",
    installTarget: "CLAUDE.md",
  },
  {
    id: "codex",
    name: "Codex",
    blurb: "OpenAI's coding CLI. Marker file lives under .codex/, install target is a tight per-tool API surface.",
    marker: ".codex/",
    installTarget: ".codex/SHIP_API.md",
  },
  {
    id: "copilot",
    name: "GitHub Copilot",
    blurb: "Lives where every team already has it — inside the IDE, fed by the conventional .github/copilot-instructions.md path.",
    marker: ".github/copilot-instructions.md",
    installTarget: ".github/copilot-instructions.md",
  },
];

const ADJACENT: AdjacentAgent[] = [
  { id: "agents-md", note: "Generic AGENTS.md convention; the cross-tool baseline." },
  { id: "claude-md", note: "CLAUDE.md without the local Claude CLI tooling." },
  { id: "aider", note: "Pair-programming CLI with its own AIDER.md root." },
  { id: "cline", note: "VS Code extension; reads .clinerules." },
  { id: "continue", note: "Open-source IDE plugin (VS Code / JetBrains)." },
  { id: "windsurf", note: "Codeium's IDE; reads .windsurfrules." },
  { id: "zed", note: "Zed editor with AI; rule body under .zed/." },
  { id: "gemini", note: "Google's CLI; reads GEMINI.md." },
  { id: "opencode", note: "OpenCode CLI; rule body under .opencode/." },
  { id: "cursor-cloud", note: "Cursor's cloud agents — early hint of the cloud-execution direction." },
];

const FULL_MATRIX: Agent[] = [
  { id: "cursor", marker: ".cursor/, .cursor/rules/", installTarget: ".cursor/rules/ship-artifacts-protocol.mdc", adapter: "collection/agent-rules-cursor", invoke: "Open repo in Cursor IDE", initCommand: "shipctl init --agents cursor --copy-rules", tier: "core" },
  { id: "claude", marker: "CLAUDE.md, .claude/", installTarget: "CLAUDE.md", adapter: "collection/agent-rules-claude", invoke: "Claude Code, local + SaaS", initCommand: "shipctl init --agents claude --copy-rules", tier: "core" },
  { id: "codex", marker: ".codex/", installTarget: ".codex/SHIP_API.md", adapter: "collection/agent-rules-codex", invoke: "npm i -g @openai/codex, then codex in repo", initCommand: "shipctl init --agents codex --copy-rules", tier: "core" },
  { id: "copilot", marker: ".github/copilot-instructions.md", installTarget: ".github/copilot-instructions.md", adapter: "collection/agent-rules-copilot", invoke: "GitHub Copilot in IDE", initCommand: "shipctl init --agents copilot --copy-rules", tier: "core" },
  { id: "agents-md", marker: "AGENTS.md", installTarget: "AGENTS.md", adapter: "collection/agent-rules-agents-md", invoke: "Generic / Codex convention; file at repo root", initCommand: "shipctl init --agents agents-md --copy-rules", tier: "adjacent" },
  { id: "claude-md", marker: "CLAUDE.md", installTarget: "CLAUDE.md", adapter: "collection/agent-rules-claude-md", invoke: "Install Claude Code CLI, claude in repo", initCommand: "shipctl init --agents claude-md --copy-rules", tier: "adjacent" },
  { id: "aider", marker: ".aider.conf.yml, .aider/, AIDER.md", installTarget: "AIDER.md", adapter: "collection/agent-rules-aider", invoke: "pipx install aider-chat, then aider in repo", initCommand: "shipctl init --agents aider --copy-rules", tier: "adjacent" },
  { id: "cline", marker: ".clinerules, .rooignore", installTarget: ".clinerules", adapter: "collection/agent-rules-cline", invoke: "Cline / Roo Cline extension in VS Code", initCommand: "shipctl init --agents cline --copy-rules", tier: "adjacent" },
  { id: "continue", marker: ".continue/config.json", installTarget: ".continue/ship.md", adapter: "collection/agent-rules-continue", invoke: "Continue.dev extension in VS Code / JetBrains", initCommand: "shipctl init --agents continue --copy-rules", tier: "adjacent" },
  { id: "windsurf", marker: ".windsurfrules", installTarget: ".windsurfrules", adapter: "collection/agent-rules-windsurf", invoke: "Windsurf IDE (Codeium)", initCommand: "shipctl init --agents windsurf --copy-rules", tier: "adjacent" },
  { id: "zed", marker: ".zed/, .zed/settings.json", installTarget: ".zed/ship.md", adapter: "collection/agent-rules-zed", invoke: "Zed editor with AI enabled", initCommand: "shipctl init --agents zed --copy-rules", tier: "adjacent" },
  { id: "gemini", marker: "GEMINI.md, .gemini/", installTarget: "GEMINI.md", adapter: "collection/agent-rules-gemini", invoke: "npm i -g @google/gemini-cli, then gemini in repo", initCommand: "shipctl init --agents gemini --copy-rules", tier: "adjacent" },
  { id: "opencode", marker: ".opencode/", installTarget: ".opencode/ship.md", adapter: "collection/agent-rules-opencode", invoke: "OpenCode CLI", initCommand: "shipctl init --agents opencode --copy-rules", tier: "adjacent" },
  { id: "cursor-cloud", marker: ".cursor/environments.json", installTarget: ".cursor/environments.json (marker-guarded)", adapter: "collection/agent-rules-cursor-cloud", invoke: "Cursor Cloud Agents", initCommand: "shipctl init --agents cursor-cloud --copy-rules", tier: "adjacent" },
];

type Ambition = { kicker: string; title: string; body: string; accent: "aqua" | "lilac" | "sun" | "coral" };

const AMBITIONS: Ambition[] = [
  {
    kicker: "Portability",
    title: "One contract across every agent",
    body: "Swap Cursor for Codex tomorrow without rewriting a single pattern. <kind>:<id>@<version>, marker-delimited, the same install target shape — that's the promise the fourteen rows in the table are working toward.",
    accent: "aqua",
  },
  {
    kicker: "Drift detection",
    title: "shipctl verify that reads like a code review",
    body: "Your rule file is at version X; the catalog ships Y; here is the diff. No human has to remember version strings, no team discovers a six-month-old drift on a Friday.",
    accent: "lilac",
  },
  {
    kicker: "Adoption",
    title: "Same five-step path, every time",
    body: "Install the App. Activate the repo. Pick your agent in .ship/config.yml. Run shipctl init. Open the workspace. The shape doesn't change with the agent — that's how we keep onboarding from being a tribal call.",
    accent: "sun",
  },
  {
    kicker: "Cloud execution",
    title: "Routines that run wherever the workspace decides",
    body: "The cursor-cloud row is the early hint. Routines that run \"on a developer's laptop\" today should run wherever the workspace decides — a CI runner, a hosted agent, a long-lived sandbox — without rewriting the rule body.",
    accent: "coral",
  },
];

const OUT_OF_SCOPE = [
  {
    title: "We are not trying to be the model",
    body: "The contract is the agent's rule body and the artifact protocol. The model behind the agent — GPT, Claude, Gemini, whatever ships next — is replaceable. We don't pick winners; we pick contracts.",
  },
  {
    title: "We are not merging editor UI patterns",
    body: "Each agent reads from its own file in its own conventions. Cursor reads .cursor/rules/. Copilot reads .github/copilot-instructions.md. Ship adapts; we don't try to make the editors converge.",
  },
  {
    title: "We are not shipping a fifty-runtime shallow matrix",
    body: "The temptation to wrap a thin abstraction layer over every editor that has ever existed is real, and we say no to it. Fewer-but-stronger beats many-but-shallow. The four core runtimes are deep on purpose.",
  },
];

const ACCENT_RING: Record<Ambition["accent"], string> = {
  aqua: "border-aqua/35 hover:border-aqua/60",
  lilac: "border-lilac/35 hover:border-lilac/60",
  sun: "border-sun/35 hover:border-sun/60",
  coral: "border-coral/35 hover:border-coral/60",
};

const ACCENT_KICKER: Record<Ambition["accent"], string> = {
  aqua: "text-aqua/85",
  lilac: "text-lilac/85",
  sun: "text-sun/85",
  coral: "text-coral/85",
};

export default function RoadmapPage() {
  return (
    <>
      <SiteHeader />
      <main>
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-10%,rgba(46,230,214,0.18),transparent_55%)]" />
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_40%_at_85%_30%,rgba(209,167,255,0.12),transparent_60%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/90">Roadmap</p>
            <h1 className="font-display mt-4 text-4xl font-bold text-white sm:text-5xl lg:text-6xl">
              What we ship today, and what we&apos;re growing toward
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              Ship is agent-runtime agnostic on purpose. Four runtimes carry the weight of production today; the next ten are
              shipped, working, and waiting for early adopters to feed back what they learn. The trajectory is one contract
              across every IDE and CLI agent — and routines that run wherever the workspace decides.
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <Link href="#core" className="btn-primary inline-flex">
                Today&apos;s production depth
              </Link>
              <Link href="#ambitions" className="btn-secondary inline-flex">
                What we&apos;re growing toward
              </Link>
            </div>
          </div>
        </section>

        <section id="core" className="scroll-mt-24 border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Today</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              The core four
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              The runtimes a new team should pick from. Most teams in the beta use one of these; the rule body contract and the
              marker protocol are mature; we test against these in CI and they carry real Automations under real load.
            </p>

            <div className="mt-10 grid gap-5 md:grid-cols-2">
              {CORE.map((agent) => (
                <div
                  key={agent.id}
                  className="rounded-2xl border border-white/12 bg-gradient-to-br from-white/[0.06] via-white/[0.02] to-transparent p-6 shadow-card transition hover:border-aqua/35"
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <h3 className="font-display text-2xl font-bold text-white">{agent.name}</h3>
                    <code className="rounded-full border border-aqua/30 bg-aqua/10 px-3 py-1 text-[11px] font-semibold tracking-wide text-aqua">
                      {agent.id}
                    </code>
                  </div>
                  <p className="mt-3 text-sm leading-relaxed text-white/70">{agent.blurb}</p>
                  <div className="mt-5 grid gap-2 text-xs">
                    <div className="flex items-baseline gap-2">
                      <span className="font-semibold uppercase tracking-[0.16em] text-white/45">Marker</span>
                      <code className="font-mono text-white/80">{agent.marker}</code>
                    </div>
                    <div className="flex items-baseline gap-2">
                      <span className="font-semibold uppercase tracking-[0.16em] text-white/45">Install</span>
                      <code className="font-mono text-white/80">{agent.installTarget}</code>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-lilac/85">Adjacent</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Ten more runtimes, working and waiting for early adopters
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              Same contract, thinner production usage. The rule body and install target ship; the operator stories haven&apos;t
              piled up yet. If you pick from this list, expect to be the early adopter and to feed back what you learn.
            </p>

            <div className="mt-10 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {ADJACENT.map((agent) => (
                <div
                  key={agent.id}
                  className="rounded-xl border border-white/10 bg-white/[0.025] p-4 transition hover:border-lilac/35"
                >
                  <code className="font-mono text-sm font-semibold text-lilac">{agent.id}</code>
                  <p className="mt-2 text-sm leading-relaxed text-white/65">{agent.note}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-sun/85">Snapshot</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              The full matrix, today
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              Marker file, install target, adapter artifact, invocation, and the example <code className="font-mono text-sun/90">shipctl init</code> command for
              every supported runtime. Bumping the artifact bumps the footer marker; <code className="font-mono text-sun/90">shipctl verify --check rules-markers</code> reports drift.
            </p>

            <div className="mt-8 overflow-x-auto rounded-2xl border border-white/10 bg-black/20">
              <table className="w-full min-w-[900px] text-left text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-[11px] font-semibold uppercase tracking-[0.16em] text-white/55">
                    <th className="px-4 py-3">Agent</th>
                    <th className="px-4 py-3">Marker</th>
                    <th className="px-4 py-3">Install target</th>
                    <th className="px-4 py-3">Adapter artifact</th>
                    <th className="px-4 py-3">Invoke</th>
                  </tr>
                </thead>
                <tbody>
                  {FULL_MATRIX.map((agent) => (
                    <tr
                      key={agent.id}
                      className={`border-b border-white/5 last:border-b-0 ${agent.tier === "core" ? "bg-aqua/[0.03]" : ""}`}
                    >
                      <td className="px-4 py-3 align-top">
                        <code className={`font-mono text-sm font-semibold ${agent.tier === "core" ? "text-aqua" : "text-lilac"}`}>
                          {agent.id}
                        </code>
                        {agent.tier === "core" && (
                          <span className="ml-2 align-middle text-[10px] font-semibold uppercase tracking-[0.18em] text-aqua/70">
                            core
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 align-top font-mono text-xs text-white/75">{agent.marker}</td>
                      <td className="px-4 py-3 align-top font-mono text-xs text-white/75">{agent.installTarget}</td>
                      <td className="px-4 py-3 align-top font-mono text-xs text-white/60">{agent.adapter}</td>
                      <td className="px-4 py-3 align-top text-xs text-white/65">{agent.invoke}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-6 rounded-xl border border-white/10 bg-black/20 p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-white/55">Multiple agents at once</p>
              <pre className="mt-2 overflow-x-auto font-mono text-sm text-white/85">
                <code>{`npx @elmundi/ship-cli init --agents cursor,codex,claude-md,aider --copy-rules`}</code>
              </pre>
            </div>
          </div>
        </section>

        <section id="ambitions" className="scroll-mt-24 border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-coral/85">Ambitions</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              What we&apos;re growing toward
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              The fourteen rows in the table are not an end state. They are scaffolding for four ambitions — portability, drift
              detection, repeatable adoption, cloud execution — that the contract is being shaped to support.
            </p>

            <div className="mt-10 grid gap-5 md:grid-cols-2">
              {AMBITIONS.map((a) => (
                <div
                  key={a.title}
                  className={`rounded-2xl border bg-white/[0.025] p-6 transition ${ACCENT_RING[a.accent]}`}
                >
                  <p className={`text-[11px] font-bold uppercase tracking-[0.18em] ${ACCENT_KICKER[a.accent]}`}>{a.kicker}</p>
                  <h3 className="font-display mt-3 text-xl font-bold text-white">{a.title}</h3>
                  <p className="mt-3 text-sm leading-relaxed text-white/70">{a.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-white/55">Out of scope, on purpose</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Three things we are deliberately not chasing
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              Saying yes to everything is how a tool ages into a vendor demo. Saying no on purpose is how a contract stays
              defendable.
            </p>

            <div className="mt-10 grid gap-5 md:grid-cols-3">
              {OUT_OF_SCOPE.map((item) => (
                <div
                  key={item.title}
                  className="rounded-2xl border border-white/10 bg-white/[0.02] p-6"
                >
                  <h3 className="font-display text-lg font-bold text-white">{item.title}</h3>
                  <p className="mt-3 text-sm leading-relaxed text-white/65">{item.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="bg-black/30 py-16 sm:py-20">
          <div className="mx-auto max-w-3xl px-4 text-center sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Next</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Pick a runtime, run the contract
            </h2>
            <p className="mt-4 text-base leading-relaxed text-white/70">
              The catalog you see today is the snapshot. The trajectory is what matters. Pick from the four core runtimes, or
              jump to an adjacent one if you&apos;re building the next generation of tooling and want to feed back what you
              learn. Either way, the contract is the same — and that is the whole point.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link href="/beta" className="btn-primary inline-flex">
                Get started
              </Link>
              <Link href="/book#what-you-are-actually-buying" className="btn-secondary inline-flex">
                What you are actually buying
              </Link>
              <Link href="/docs/developer/ship-folder" className="btn-secondary inline-flex">
                Wire it into a repo
              </Link>
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
