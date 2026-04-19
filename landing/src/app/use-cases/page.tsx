import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Customer stories — Ship",
  description:
    "Enterprise teams use Ship to roll out agentic SDLC without trading governance for speed. Read how reference deployments wired Linear, GitHub Actions, and AI agents into a single, auditable delivery loop.",
};

type Story = {
  slug: string;
  industry: string;
  org: string;
  headline: string;
  outcome: string;
  bullets: string[];
  stack: string[];
};

const STORIES: Story[] = [
  {
    slug: "elmundi",
    industry: "E-commerce · D2C platform",
    org: "ElMundi",
    headline: "Cut delivery-lane drift to zero by making the SDLC a contract, not a wiki",
    outcome:
      "Replaced ad-hoc agent prompting with a scheduled, audit-friendly delivery loop on a public monorepo — every ticket walks Linear → branch → PR → Playwright → release behind named workflows.",
    bullets: [
      "Operators launch the day with one workflow, not six tabs",
      "Every agent action is reviewable in GitHub Actions and Linear history",
      "Onboarding a new contributor is a single page of the docs, not a tribal call",
    ],
    stack: ["Linear", "GitHub Actions", "Cursor Cloud Agent", "Playwright", "Sentry"],
  },
  {
    slug: "ship",
    industry: "Developer tools · Open methodology",
    org: "Ship (this kit)",
    headline: "Shipped the methodology like enterprise software so procurement stops blocking pilots",
    outcome:
      "Packaged forty chapters of operating doctrine, a versioned artifact catalog (patterns / tools / workflows / collections), a CLI, and a small agent API behind one Apache-2.0 surface — so security review is a license check, not a vendor questionnaire.",
    bullets: [
      "Apache-2.0 + public repo removes the legal-review gate before a pilot can start",
      "Docs, catalog, and CLI all read the same source of truth — no drift between sales deck and runbook",
      "Adopters compose what they need: take a single pattern, or wire the full reference stack",
    ],
    stack: ["Next.js", "FastAPI", "shipctl", "MDX", "Apache-2.0"],
  },
];

export default function UseCasesIndexPage() {
  return (
    <>
      <SiteHeader />
      <main>
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-10%,rgba(46,230,214,0.16),transparent_55%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/90">Customer stories</p>
            <h1 className="font-display mt-4 text-4xl font-bold text-white sm:text-5xl">
              Predictable agent-driven delivery, with the audit trail your CISO already asked for
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              Operators get a single button to start the day. Engineering gets diffable plays instead of prompt folklore.
              Security and procurement read the same artifacts the team runs. Below: how reference deployments wired Ship
              into their SDLC — and what that bought them.
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <Link href="/getting-started" className="btn-primary inline-flex">
                Start a 30-day pilot
              </Link>
              <Link href="#stories" className="btn-secondary inline-flex">
                Read the stories
              </Link>
            </div>
          </div>
        </section>

        {/* Why customers buy Ship */}
        <section className="border-b border-white/10 py-14 sm:py-16">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-widest text-white/45">Why teams adopt Ship</p>
            <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">
              The delivery problem agentic SDLC actually solves
            </h2>
            <div className="mt-10 grid gap-6 md:grid-cols-3">
              {WHY.map((w) => (
                <div key={w.title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
                  <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-aqua/85">{w.kicker}</p>
                  <h3 className="mt-3 font-display text-lg font-bold text-white">{w.title}</h3>
                  <p className="mt-3 text-sm leading-relaxed text-white/65">{w.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Stories */}
        <section id="stories" className="scroll-mt-24 py-16 sm:py-20">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-widest text-sun/90">Stories</p>
            <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">Reference deployments</h2>
            <p className="mt-3 max-w-2xl text-sm text-white/55">
              Each case follows the buyer arc: <strong className="text-white">situation</strong>,{" "}
              <strong className="text-white">complication</strong>, <strong className="text-white">resolution</strong>,{" "}
              <strong className="text-white">measured outcome</strong>, <strong className="text-white">evidence</strong>.
              Engineering depth lives in the docs; this page is for buying committees.
            </p>

            <div className="mt-10 flex flex-col gap-8">
              {STORIES.map((s) => (
                <Link
                  key={s.slug}
                  href={`/use-cases/${s.slug}`}
                  className="group block rounded-3xl border border-white/12 bg-gradient-to-br from-white/[0.08] via-white/[0.02] to-transparent p-8 shadow-card transition hover:border-aqua/35 hover:shadow-glow"
                >
                  <div className="flex flex-wrap items-center gap-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-white/55">
                    <span>{s.industry}</span>
                    <span aria-hidden className="text-white/20">
                      ·
                    </span>
                    <span className="text-aqua/85">{s.org}</span>
                  </div>
                  <h3 className="font-display mt-4 text-2xl font-bold text-white group-hover:text-aqua sm:text-3xl">
                    {s.headline}
                  </h3>
                  <p className="mt-4 max-w-3xl text-base leading-relaxed text-white/75">{s.outcome}</p>
                  <ul className="mt-6 grid gap-2 text-sm text-white/70 sm:grid-cols-3">
                    {s.bullets.map((b) => (
                      <li key={b} className="flex items-start gap-2">
                        <span aria-hidden className="mt-[7px] h-1.5 w-1.5 flex-none rounded-full bg-aqua/80" />
                        <span>{b}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-6 flex flex-wrap items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-white/45">
                    {s.stack.map((t) => (
                      <span key={t} className="rounded-full border border-white/10 bg-black/30 px-2.5 py-1">
                        {t}
                      </span>
                    ))}
                  </div>
                  <span className="mt-8 inline-flex text-sm font-semibold text-aqua">Read the full story →</span>
                </Link>
              ))}
            </div>
          </div>
        </section>

        {/* Closer */}
        <section className="border-t border-white/10 bg-black/25 py-16 sm:py-20">
          <div className="mx-auto max-w-3xl px-4 text-center sm:px-6">
            <p className="text-xs font-bold uppercase tracking-widest text-white/45">Next step</p>
            <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">
              Run the same loop in your repo this week
            </h2>
            <p className="mt-4 text-base leading-relaxed text-white/70">
              The kit is open source. Most teams stand up a working delivery loop in under an hour using{" "}
              <code className="rounded bg-white/10 px-1.5 py-0.5 text-aqua/90">shipctl init</code>. The deck-friendly story
              is on this page; the rest is the docs and a CLI you can read end-to-end on the plane home.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link href="/getting-started" className="btn-primary inline-flex">
                Get started
              </Link>
              <Link href="/book" className="btn-secondary inline-flex">
                Read the book
              </Link>
              <Link href="/patterns" className="btn-secondary inline-flex">
                Browse patterns
              </Link>
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}

const WHY: { kicker: string; title: string; body: string }[] = [
  {
    kicker: "Throughput",
    title: "More tickets per engineer-hour, with the same review bar",
    body: "Agents take the boring half of every ticket — branch, scaffold, write the failing test, draft the PR — so engineers spend their time on judgement, not setup. The Ship loop holds the delivery lane to the same code-review and QA standard whether a human or an agent did the typing.",
  },
  {
    kicker: "Governance",
    title: "Audit trail is a side-effect, not a project",
    body: "Every action — pick, branch, PR, test run, merge — leaves a receipt in your existing tools (Linear, GitHub Actions, Sentry). Security and compliance read the same artifacts engineers diff, instead of asking for monthly screenshot exports.",
  },
  {
    kicker: "Risk",
    title: "Boring architecture, no proprietary control plane",
    body: "Markdown in git, manifests as catalogs, GitHub Actions for scheduling. There is no Ship server holding your secrets or your code. Apache-2.0 license, public repo, runs on infrastructure your team already owns and audits.",
  },
];
