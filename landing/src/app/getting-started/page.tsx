import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { WaitlistSection } from "@/components/waitlist-section";

export const metadata: Metadata = {
  title: "Getting started — Ship",
  description:
    "Start Ship from the founder workspace: connect a repo, bind the tracker, set policies, seed knowledge, and keep decisions tied to evidence.",
};

const SETUP_STEPS: { title: string; body: string; proof: string }[] = [
  {
    title: "Create the workspace",
    body: "Name the company, product, or product area that owns the outcome. Invite the founder, product owner, reviewers, and anyone who owns production risk.",
    proof: "A workspace with members and admin policy.",
  },
  {
    title: "Connect GitHub and activate repos",
    body: "Install the Ship GitHub App, choose the repositories Ship may observe, and let the console show which repos need template or workflow updates.",
    proof: "Connected repos with current Ship bundle status.",
  },
  {
    title: "Bind the tracker",
    body: "Connect Linear, GitHub Issues, Jira, or the tracker your team already uses. Tracker states and owners are the record of human intent.",
    proof: "Work in progress and blockers point back to tickets.",
  },
  {
    title: "Set policies and seed knowledge",
    body: "Add repo facts, product constraints, standing rules, and review policy through the console so agents have boundaries before they act.",
    proof: "Knowledge pages and policies are visible to admins.",
  },
  {
    title: "Review the dashboard and Inbox",
    body: "Use the workspace home to see health, shipped work, active work, and automation signals. Use the Inbox for clarifications, improvements, approvals, and failures that need a decision.",
    proof: "Each item has an owner, a next action, and evidence.",
  },
];

const STACK_EXPECTATIONS: { label: string; body: string }[] = [
  {
    label: "A clear owner",
    body: "Someone can say what is allowed to move, what risk is acceptable, and when work is done.",
  },
  {
    label: "A tracker with states",
    body: "Work has a visible place to wait, move, block, review, and finish. Automation does not replace that story.",
  },
  {
    label: "A connected repository",
    body: "Pull requests, checks, workflow runs, and configuration stay traceable to the product work they support.",
  },
  {
    label: "Evidence in the record",
    body: "Important decisions and machine actions link to tickets, PRs, comments, checks, or knowledge pages.",
  },
  {
    label: "Secrets outside prompts",
    body: "Agent and repo credentials live in the platform or host secret store, never in instructions copied into chat.",
  },
  {
    label: "Policies before automation",
    body: "Allowed actions, review gates, required evidence, and escalation points are explicit before agents move work.",
  },
];

const NEXT_LINKS: { href: string; title: string; body: string }[] = [
  {
    href: "/docs/concepts",
    title: "Product concepts",
    body: "Workspace, repo, tracker, Inbox, knowledge, evidence, and automation in plain language.",
  },
  {
    href: "/docs/knowledge-buckets",
    title: "Knowledge",
    body: "How Ship stores repo and product context so agents stop guessing.",
  },
  {
    href: "/docs/automations",
    title: "Automations",
    body: "How repeatable checks and agent-assisted actions stay bounded and auditable.",
  },
  {
    href: "/docs/operating",
    title: "Operating",
    body: "How to review blockers, shipped work, decisions, and evidence after setup.",
  },
  {
    href: "/use-cases",
    title: "Use cases",
    body: "How different owners use Ship without changing the core method.",
  },
];

export default function GettingStartedPage() {
  return (
    <div className="min-h-screen bg-ink pt-16">
      <SiteHeader />
      <main className="mx-auto max-w-5xl px-4 py-10 sm:px-6 sm:py-12 lg:py-16">
        <header className="docs-hero">
          <p className="docs-hero-kicker">Workspace setup</p>
          <h1 className="docs-hero-title">Start with the owner workspace</h1>
          <p className="docs-hero-lede">
            Ship works best when the solo founder or product owner can see the same delivery story engineers can audit:
            which repo is connected, which tracker item is moving, what policy applies, what needs a decision, and where
            the evidence lives.
          </p>
        </header>

        <section className="mt-12">
          <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
            <div>
              <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">
            1 · Wire the owner workspace
              </h2>
              <p className="mt-3 max-w-3xl text-base text-white/65 sm:text-lg">
                Use this sequence whether you are testing one repo or bringing a team into Ship. Each step should leave a
                visible record, not a private promise.
              </p>
            </div>
            <Link className="btn-secondary" href="/docs">
              Browse docs
            </Link>
          </div>

          <ol className="space-y-4">
            {SETUP_STEPS.map((step, index) => (
              <li
                key={step.title}
                className="rounded-2xl border border-white/10 bg-white/[0.03] p-5"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
                  <span className="font-display shrink-0 text-2xl font-bold text-aqua">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <div>
                    <h3 className="font-display text-xl font-bold text-white">{step.title}</h3>
                    <p className="mt-2 text-sm leading-relaxed text-white/70">{step.body}</p>
                    <p className="mt-3 rounded-xl border border-aqua/20 bg-aqua/[0.05] px-3 py-2 text-xs font-semibold text-aqua/90">
                      Proof: {step.proof}
                    </p>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="mt-16">
          <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">
            2 · What Ship expects from the stack
          </h2>
          <p className="mt-3 max-w-3xl text-base text-white/65 sm:text-lg">
            Ship is opinionated about responsibility, not vendor logos. If your tools can express the expectations below,
            the method can stay portable.
          </p>

          <ul className="mt-8 grid gap-3 sm:grid-cols-2">
            {STACK_EXPECTATIONS.map((row) => (
              <li
                key={row.label}
                className="rounded-2xl border border-white/10 bg-white/[0.03] p-5"
              >
                <p className="font-display text-base font-bold text-white">{row.label}</p>
                <p className="mt-2 text-sm leading-relaxed text-white/65">{row.body}</p>
              </li>
            ))}
          </ul>
        </section>

        <section className="mt-16">
          <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">
            3 · Request closed&hyphen;beta access
          </h2>
          <p className="mt-3 max-w-3xl text-base text-white/65 sm:text-lg">
            Tell us about your team and the problems you&apos;re solving. We review applications weekly.
          </p>
          <div className="mt-8 max-w-xl">
            <WaitlistSection />
          </div>
        </section>

        <section className="mt-16">
          <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">4 · Where to go next</h2>
          <div className="mt-8 grid gap-4 sm:grid-cols-2">
            {NEXT_LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="group flex flex-col gap-2 rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.04] to-transparent p-5 transition hover:border-aqua/40 hover:bg-white/[0.06]"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-display text-base font-bold text-white group-hover:text-aqua">{link.title}</span>
                  <span className="text-aqua/70 transition group-hover:translate-x-0.5">→</span>
                </div>
                <p className="text-sm leading-relaxed text-white/60">{link.body}</p>
              </Link>
            ))}
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
