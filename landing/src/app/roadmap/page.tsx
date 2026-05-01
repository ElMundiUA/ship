import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Roadmap — Ship",
  description:
    "From sign-up to first closed ticket: one day for SMBs, one week for enterprise. What's live now, what's next, what we're growing toward — and the things we deliberately won't build.",
};

type Item = {
  title: string;
  business: string;
  technical: string;
};

const NOW: Item[] = [
  {
    title: "Development process — production depth",
    business: "Eight states from intake to PR review carry product work end to end with a named owner at every step.",
    technical: "Live in workspace `/process` with state transitions, capacity guards, and tracker mapping for Linear / Jira / GitHub Issues / GitLab / Azure DevOps.",
  },
  {
    title: "Inbox — five item types, five canonical actions",
    business: "Clarifications, improvements, approvals, failures, exceptions — all routed to the right person, all closed with a recorded reason.",
    technical: "Workspace-scoped routing rules bind handles to users, groups, or built-in strategies (`round_robin` and friends). Side-effect-free preview before save.",
  },
  {
    title: "Knowledge buckets + distiller",
    business: "Context that agents and reviewers lean on, reviewable like code, supersedeable when the rule changes.",
    technical: "Mirrored from `.ship/knowledge/*.md` in connected repos, imported from Notion / Confluence / Firecrawl-backed websites. Distiller routes raw notes to the right bucket.",
  },
  {
    title: "Workspace + member roles + integrations",
    business: "Owner, admin, member — three roles with last-owner protection. Integrations in Settings, secrets in the workspace store, audit log on every privileged change.",
    technical: "GitHub App for repo activation, OAuth/PAT trackers, Slack/Teams/OTel/S3 export sinks, encrypted workspace secret store with per-event audit.",
  },
];

const NEXT: Item[] = [
  {
    title: "Sign-up to first closed ticket",
    business: "One day for small companies, one week for enterprise. The full path — workspace, repo, tracker, members, policies, first routine — finished in a single working session.",
    technical: "Wizard polish, smart defaults per repo size and tracker shape, inline guidance, and onboarding telemetry that flags stuck cohorts before they churn.",
  },
  {
    title: "Navigator — best AI assistant in the industry",
    business: "An assistant that knows your workspace cold: what's in flight, why it stalled, which knowledge applies, what to do next. Not a chatbot — a navigator.",
    technical: "Workspace-aware retrieval over Inbox, processes, knowledge, audit, and tracker context with topic-shift detection and explicit save-to-bucket flow.",
  },
  {
    title: "More processes",
    business: "Marketing, documentation, testing improvements, sales, and support — using the same specialists already in the catalogue.",
    technical: "Drafted shapes on `/process` with `marketing_operator`, `support_success`, `technical_writer`, `qa_automation`, and `data_ml_engineer` specialists. Each lifted from draft to production depth one process at a time.",
  },
  {
    title: "Wider integration base",
    business: "Talk to Ship from the tools your team already uses — Slack, Teams, Discord — and bind to whatever tracker the company runs on.",
    technical: "More orchestrators, more trackers, native chat-app surfaces (Slack/Teams/Discord) for routed Inbox items and routine output, not just notification hooks.",
  },
];

const LATER: Item[] = [
  {
    title: "Mobile + voice",
    business: "Approve, clarify, and decide from your phone. Open the Inbox by voice on the way in. The workspace travels with the operator.",
    technical: "Native iOS / Android apps with push routing, plus voice intake for clarification answers and approval flows — same Inbox contract, new surface.",
  },
  {
    title: "Custom desktop app",
    business: "An always-on workspace surface that lives outside the browser and survives the next tab close.",
    technical: "Electron / Tauri shell wrapping the console with native notifications, persistent agent sessions, and offline-tolerant sync.",
  },
  {
    title: "Release process automation",
    business: "Promotions and rollbacks become a process like any other — with states, owners, and evidence that survives the ship-it adrenaline.",
    technical: "Release as a top-level process with environment progression, rollback approvals, and post-release retro routines tied to the same audit log.",
  },
  {
    title: "Compliance & security certifications",
    business: "SOC 2 Type II, ISO 27001, and the rest of the procurement checklist enterprise customers ask for.",
    technical: "Audit-log retention guarantees, evidence collection automated through compliance routines, third-party attestation in flight.",
  },
];

const WONT_BUILD: { title: string; body: string }[] = [
  {
    title: "We won't teach you how to run your business",
    body: "You know your customers, your roadmap, and your constraints better than any tool ever will. Ship adapts to how you already work — it doesn't push opinions about strategy onto teams that already have them.",
  },
  {
    title: "We won't transform your processes",
    body: "We automate the processes you already have. If your engineering team uses Linear and Jira, your support team uses Zendesk, your marketing uses Notion — we plug into all of them. We do not ship a new operating model and expect you to migrate.",
  },
  {
    title: "We respect the difference between solo and company",
    body: "A solo developer with Claude Code or Codex doesn't need a workspace. A company with twenty contributors and three trackers does. We build for the second audience without pretending the first one is broken.",
  },
  {
    title: "We won't keep you spending months on banal projects",
    body: "Companies still pay months and millions for repeatable delivery work that should be a routine. The whole point of Ship is to make those routines reviewable, fast, and boring — so the team's expensive hours go where the judgement calls actually live.",
  },
];

type Agent = {
  id: string;
  marker: string;
  installTarget: string;
  tier: "core" | "adjacent";
};

const AGENT_MATRIX: Agent[] = [
  { id: "cursor", marker: ".cursor/, .cursor/rules/", installTarget: ".cursor/rules/ship-artifacts-protocol.mdc", tier: "core" },
  { id: "claude", marker: "CLAUDE.md, .claude/", installTarget: "CLAUDE.md", tier: "core" },
  { id: "codex", marker: ".codex/", installTarget: ".codex/SHIP_API.md", tier: "core" },
  { id: "copilot", marker: ".github/copilot-instructions.md", installTarget: ".github/copilot-instructions.md", tier: "core" },
  { id: "agents-md", marker: "AGENTS.md", installTarget: "AGENTS.md", tier: "adjacent" },
  { id: "claude-md", marker: "CLAUDE.md", installTarget: "CLAUDE.md", tier: "adjacent" },
  { id: "aider", marker: ".aider.conf.yml, AIDER.md", installTarget: "AIDER.md", tier: "adjacent" },
  { id: "cline", marker: ".clinerules", installTarget: ".clinerules", tier: "adjacent" },
  { id: "continue", marker: ".continue/config.json", installTarget: ".continue/ship.md", tier: "adjacent" },
  { id: "windsurf", marker: ".windsurfrules", installTarget: ".windsurfrules", tier: "adjacent" },
  { id: "zed", marker: ".zed/, .zed/settings.json", installTarget: ".zed/ship.md", tier: "adjacent" },
  { id: "gemini", marker: "GEMINI.md, .gemini/", installTarget: "GEMINI.md", tier: "adjacent" },
  { id: "opencode", marker: ".opencode/", installTarget: ".opencode/ship.md", tier: "adjacent" },
  { id: "cursor-cloud", marker: ".cursor/environments.json", installTarget: ".cursor/environments.json", tier: "adjacent" },
];

export default function RoadmapPage() {
  return (
    <>
      <SiteHeader />
      <main>
        {/* Hero */}
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_70%_45%_at_50%_-10%,rgba(46,230,214,0.12),transparent_55%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">North star</p>
            <h1 className="font-display mt-4 text-4xl font-bold text-white sm:text-5xl lg:text-[3.25rem] lg:leading-[1.06]">
              From sign-up to your first closed ticket: one day for SMB, one week for enterprise.
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-base text-white/65 sm:text-lg">
              That&apos;s the bar. Everything below is what&apos;s live, what&apos;s in motion, and what we&apos;re
              growing toward to keep that bar honest at every team size.
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <Link href="#now" className="btn-primary inline-flex">
                What&apos;s live
              </Link>
              <Link href="#next" className="btn-secondary inline-flex">
                What&apos;s next
              </Link>
            </div>
          </div>
        </section>

        {/* Now / Next / Later columns */}
        <section className="border-b border-white/10 py-14 sm:py-20">
          <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
            <div id="now" className="scroll-mt-24">
              <ColumnHeader kicker="Live now · Q2 2026" title="Production depth" accent="aqua" />
              <ItemGrid items={NOW} accent="aqua" />
            </div>

            <div id="next" className="mt-16 scroll-mt-24 sm:mt-20">
              <ColumnHeader kicker="Next · Q3 2026" title="In active build" accent="sun" />
              <ItemGrid items={NEXT} accent="sun" />
            </div>

            <div id="later" className="mt-16 scroll-mt-24 sm:mt-20">
              <ColumnHeader kicker="Later · Q4 2026 → 2027" title="Growing toward" accent="lilac" />
              <ItemGrid items={LATER} accent="lilac" />
            </div>
          </div>
        </section>

        {/* What we won't build */}
        <section className="border-b border-white/10 py-14 sm:py-20">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-white/55">Out of scope, on purpose</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Four things we won&apos;t build
            </h2>
            <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/65">
              Saying yes to everything is how a tool ages into a vendor demo. Saying no in writing is how the contract
              stays defendable when budget and quarter both close at the same time.
            </p>
            <div className="mt-10 grid gap-5 md:grid-cols-2">
              {WONT_BUILD.map((item) => (
                <div key={item.title} className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
                  <h3 className="font-display text-lg font-bold text-white">{item.title}</h3>
                  <p className="mt-3 text-sm leading-relaxed text-white/65">{item.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Technical appendix — agent matrix */}
        <section className="border-b border-white/10 bg-black/20 py-14 sm:py-20">
          <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-white/45">Technical appendix</p>
            <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">
              Agent runtime support matrix
            </h2>
            <p className="mt-4 max-w-3xl text-sm leading-relaxed text-white/55">
              For platform engineers picking an agent runtime: fourteen runtimes ship today. The four core runtimes carry
              the weight of production; the rest are working and waiting for early adopters to feed back what they learn.
              The contract is the same across every row.
            </p>

            <div className="mt-8 overflow-x-auto rounded-2xl border border-white/10 bg-black/30">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-[10px] font-semibold uppercase tracking-[0.16em] text-white/45">
                    <th className="px-4 py-3">Tier</th>
                    <th className="px-4 py-3">Agent</th>
                    <th className="px-4 py-3">Marker</th>
                    <th className="px-4 py-3">Install target</th>
                  </tr>
                </thead>
                <tbody>
                  {AGENT_MATRIX.map((agent) => (
                    <tr
                      key={agent.id}
                      className={`border-b border-white/5 last:border-b-0 ${agent.tier === "core" ? "bg-aqua/[0.03]" : ""}`}
                    >
                      <td className="px-4 py-3 align-top">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em] ${
                            agent.tier === "core"
                              ? "border border-aqua/40 bg-aqua/10 text-aqua"
                              : "border border-white/15 bg-white/5 text-white/55"
                          }`}
                        >
                          {agent.tier}
                        </span>
                      </td>
                      <td className="px-4 py-3 align-top">
                        <code className="font-mono text-sm font-semibold text-white">{agent.id}</code>
                      </td>
                      <td className="px-4 py-3 align-top font-mono text-xs text-white/65">{agent.marker}</td>
                      <td className="px-4 py-3 align-top font-mono text-xs text-white/65">{agent.installTarget}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="bg-black/30 py-14 sm:py-20">
          <div className="mx-auto max-w-3xl px-4 text-center sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Next</p>
            <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">
              Pick a runtime, run the contract
            </h2>
            <p className="mt-4 text-base leading-relaxed text-white/70">
              The catalogue you see today is a snapshot. The trajectory is what matters. If you want to ship on it now,
              the closed beta is open by invite.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link href="/beta" className="btn-primary inline-flex">
                Request closed-beta access
              </Link>
              <Link href="/process" className="btn-secondary inline-flex">
                See the process model
              </Link>
              <Link href="/book#what-you-are-actually-buying" className="btn-secondary inline-flex">
                Read the book chapter
              </Link>
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}

function ColumnHeader({
  kicker,
  title,
  accent,
}: {
  kicker: string;
  title: string;
  accent: "aqua" | "sun" | "lilac";
}) {
  const kickerClass = accent === "aqua" ? "text-aqua/85" : accent === "sun" ? "text-sun/85" : "text-lilac/85";
  return (
    <div className="mb-8 flex items-baseline gap-3">
      <span className={`text-xs font-bold uppercase tracking-[0.2em] ${kickerClass}`}>{kicker}</span>
      <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">{title}</h2>
    </div>
  );
}

function ItemGrid({ items, accent }: { items: Item[]; accent: "aqua" | "sun" | "lilac" }) {
  const dotClass = accent === "aqua" ? "bg-aqua" : accent === "sun" ? "bg-sun" : "bg-lilac";
  const borderHoverClass =
    accent === "aqua"
      ? "hover:border-aqua/30"
      : accent === "sun"
        ? "hover:border-sun/30"
        : "hover:border-lilac/30";
  return (
    <div className="grid gap-5 md:grid-cols-2">
      {items.map((item) => (
        <div
          key={item.title}
          className={`rounded-2xl border border-white/10 bg-white/[0.02] p-6 transition ${borderHoverClass}`}
        >
          <div className="flex items-start gap-3">
            <span className={`mt-2 inline-block h-1.5 w-1.5 shrink-0 rounded-full ${dotClass}`} aria-hidden />
            <div className="min-w-0 flex-1">
              <h3 className="font-display text-base font-bold text-white sm:text-lg">{item.title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-white/70">{item.business}</p>
              <p className="mt-3 text-xs leading-relaxed text-white/45">
                <span className="font-mono uppercase tracking-[0.14em] text-white/35">tech</span>{" "}
                {item.technical}
              </p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
