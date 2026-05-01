import type { Metadata } from "next";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import {
  UseCaseCtaRow,
  UseCaseEvidenceGrid,
  UseCaseHero,
  UseCaseResults,
  UseCaseSection,
  UseCaseSnapshot,
  UseCaseStakeholders,
} from "@/components/use-case-enterprise";

export const metadata: Metadata = {
  title: "Customer story — ElMundi (reference deployment)",
  description:
    "How ElMundi runs Ship on itself: Linear, GitHub Actions, Cursor Cloud Agent, and Playwright tied into one evidence trail.",
};

const EVIDENCE = [
  {
    src: "/use-cases/elmundi-dev-home.png",
    alt: "ElMundi consumer app home on dev.elmundi.com",
    caption:
      "The product the loop ships — dev.elmundi.com home page. Every visible change here was driven by an agent-and-engineer pair walking the Ship loop.",
  },
  {
    src: "/use-cases/elmundi-dev-collections.png",
    alt: "ElMundi product pages on dev.elmundi.com",
    caption:
      "Product pages on staging. Each visible change corresponds to merged tickets that passed the same audit chain — Linear → PR → Playwright trace.",
  },
  {
    src: "/use-cases/elmundi-manual-chapter.png",
    alt: "Ship docs chapter for ElMundi reference wiring",
    caption:
      "Operator depth — the same story written in the docs (/use-cases/elmundi) for engineers, security, and onboarding. One source, two audiences.",
  },
];

export default function ElmundiUseCasePage() {
  return (
    <>
      <SiteHeader />
      <main>
        <UseCaseHero
          eyebrow="Customer story · E-commerce · Reference deployment"
          title="From scattered Slack pings to one Inbox: how ElMundi runs Ship on itself"
          subtitle="A small e-commerce team replaced ad-hoc agent prompting with a reviewable product workspace: every ticket can be traced from Linear to branch, PR, Playwright evidence, release notes, and the Inbox decision that needed a human."
          executive="ElMundi is the public reference deployment for Ship. Engineering wanted agentic throughput; the founders wanted procurement-grade evidence; nobody wanted a second control plane to babysit. The team shipped both by making the workspace policies, tracker, repo, and evidence trail the system of record."
        />

        <UseCaseSnapshot
          items={[
            { label: "Industry", value: "E-commerce · D2C platform" },
            { label: "Team", value: "Engineering team of < 10" },
            { label: "Stack", value: "Linear · GitHub Actions · Cursor · Playwright" },
            { label: "Status", value: "Production · public reference org" },
          ]}
        />

        <UseCaseSection
          id="situation"
          kicker="01 · Situation"
          title="The team had agents that worked — but no operating model around them"
        >
          <p>
            ElMundi&apos;s engineering team had been using AI coding agents for months before adopting Ship. Capability was
            never the question. The question was <strong>governance</strong>: a roomful of people pasting prompts into
            chats produces working code <em>and</em> a tail of unanswered questions — which agent wrote this, against
            which spec, under which approval, and what proves it works in the browser? The pain was felt most by the
            people who do not write code: founders preparing for due diligence, the ops lead who has to explain a
            production incident, the new hire who joins on Monday and finds three undocumented &quot;ways we do tickets&quot;.
          </p>
          <p>
            On the engineering side the cost was subtler but real: <strong>operator overhead</strong>. Each morning
            consumed twenty to forty minutes of context-switching across Linear, the chat, the IDE, and Actions just to
            get the day&apos;s work moving. Agents were fast at the typing, but a human still had to be the orchestrator.
          </p>
        </UseCaseSection>

        <UseCaseSection
          id="complication"
          kicker="02 · Complication"
          title="Bolting on a vendor control-plane was off the table"
        >
          <p>
            The off-the-shelf options all came with the same trade: store your code or your secrets in someone
            else&apos;s SaaS, accept a proprietary opinion about how tickets, branches, and reviews should look, and
            replace one chat-driven blob of folklore with another. For a public-by-default e-commerce business that
            wanted to move faster <em>without</em> ballooning its third-party risk surface, that math did not work.
          </p>
          <p>
            The ask was specific: keep code, secrets, and audit trails inside the systems the team already owns; let
            engineers compose the loop from named, diffable parts; and make the whole thing readable by a non-engineer
            on the buying committee.
          </p>
        </UseCaseSection>

        <UseCaseSection id="solution" kicker="03 · Resolution" title="Treat the monorepo as the SDLC control plane">
          <p>
            ElMundi adopted Ship as an owner workspace on top of Linear and GitHub. The fix is not another dashboard —
            it is <strong>policy-first documentation</strong> plus{" "}
            <strong>automation that matches the words</strong>. Ship names the rules, owners, and evidence so security
            can inspect instead of guess.
          </p>
          <ul>
            <li>
              <strong>Linear</strong> became the spine. SDLC projects, states, and labels were standardised so an agent
              and a human can both reason about &quot;what is in flight, what is blocked, what is done&quot; without
              translation.
            </li>
            <li>
              <strong>GitHub Actions</strong> became the nervous system. Scheduled and on-demand workflows own agent
              launch, docs sync, and release hygiene. Secrets stay in GitHub, not in prompts.
            </li>
            <li>
              <strong>Cursor Cloud Agent</strong> became the execution layer. Branch-per-ticket runs, PRs as the review
              surface, no long-running &quot;pet&quot; agents.
            </li>
            <li>
              <strong>Playwright</strong> became the proof layer. Hosted browser runs attach traces and screenshots
              directly to the ticket narrative.
            </li>
            <li>
              <strong>The docs</strong> became the contract. Every workflow file, label, and state has a page under{" "}
              <code className="text-aqua">/use-cases/elmundi</code>; the page anyone reads ships the same words the
              automation enforces.
            </li>
          </ul>
        </UseCaseSection>

        <UseCaseSection id="implementation" kicker="04 · Rollout" title="Wired in days, not quarters">
          <p>
            The implementation was deliberately small. The team started with one workspace, connected Linear and GitHub,
            named the first policies they wanted to enforce (intake, planning, agent launch, release readiness), and
            pointed existing GitHub Actions at the new workflows. The first end-to-end run — a ticket in Linear, a branch
            cut by an agent, a PR with a Playwright trace, a merge — happened on day one. The follow-up week was spent
            tightening labels, naming conventions, and the owner rota for failed runs.
          </p>
          <p>
            What did <em>not</em> happen is also worth naming. There was no migration project, no &quot;adoption
            committee&quot;, no parallel staging org, no rewriting of existing code to suit a vendor schema. The workspace is
            additive; the existing CI, branch protection rules, and Linear automations stayed exactly as they were.
          </p>
        </UseCaseSection>

        <UseCaseResults
          title="What changed once the loop was running"
          caveat="Results are directional indicators reported by the ElMundi team operating the public reference deployment. The loop, the workflows, and the docs cited here are open source — adopters can reproduce the wiring and measure the same intervals in their own org."
          results={[
            {
              headline: "Hours, not days",
              label: "From idea to merged PR",
              detail:
                "Routine tickets — bug fixes, copy changes, schema updates — go from intake to a merged PR in a single working session, instead of slipping across days while operators reassemble context.",
            },
            {
              headline: "Single source",
              label: "Docs = automation = sales deck",
              detail:
                "The same words describe the loop to engineers (docs), to procurement (this page), and to the runtime (workflow files). Drift between &quot;how we say we work&quot; and &quot;how we actually work&quot; is structurally impossible.",
            },
            {
              headline: "Zero",
              label: "New control planes to license",
              detail:
                "No new SaaS to procure, no secrets leaving GitHub, no proprietary API in the critical path. The audit trail lives in systems your team already pays for and your auditors already accept.",
            },
          ]}
        />

        <UseCaseStakeholders
          items={[
            {
              role: "Engineering lead",
              perspective:
                "Tickets close in fewer hops because the agent and the human share a vocabulary. Code review effort moved from setup-and-context-rebuild to the part that actually needs judgement.",
            },
            {
              role: "Founder / GM",
              perspective:
                "I can show a buyer the production site and the docs page that explains how it got built. There is no demo gap — the deck and the repository tell the same story.",
            },
            {
              role: "Security / compliance",
              perspective:
                "Every agent action is a Linear state transition, a labelled branch, an Actions log line, and a PR. The chain of custody is the existing GitHub + Linear chain — nothing new to audit, nothing new to attest.",
            },
            {
              role: "New contributor",
              perspective:
                "I read one docs page and one config file and I know what to do on a Monday morning. The names in the docs match the names in the workflow files match the names in Linear.",
            },
          ]}
        />

        <UseCaseEvidenceGrid
          items={EVIDENCE}
          evidenceIntro="Evidence comes from two places: the live consumer site (dev.elmundi.com) and the operator docs. Both render from the same monorepo the team ships into."
        />

        <UseCaseCtaRow
          links={[
            { href: "/beta", label: "Adopt the same loop", variant: "primary" },
            { href: "/use-cases/elmundi", label: "Read the engineering chapter", variant: "secondary" },
            { href: "/use-cases", label: "All customer stories", variant: "secondary" },
          ]}
        />
      </main>
      <SiteFooter />
    </>
  );
}
