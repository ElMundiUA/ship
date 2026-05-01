import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Customer stories — Ship",
  description:
    "Solo founders, product owners, and engineering teams use Ship to adopt AI-assisted delivery without losing ownership, policies, evidence, or review discipline.",
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
    headline: "From scattered Slack pings to one Inbox: how ElMundi runs Ship on itself",
    outcome:
      "Replaced ad-hoc agent prompting with a workspace where each ticket can be traced from tracker intent to branch, PR, checks, release evidence, and decision owner.",
    bullets: [
      "Product and engineering leads start from one attention surface, not six tabs",
      "Every agent action is reviewable in GitHub Actions and Linear history",
      "Onboarding a new contributor is a single page of the docs, not a tribal call",
    ],
    stack: ["Linear", "GitHub Actions", "Cursor Cloud Agent", "Playwright", "Sentry"],
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
              AI-assisted delivery founders and product owners can explain after the demo
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              The reference deployments below share the same discipline: humans own intent, automations stay bounded,
              decisions land in the Inbox, and evidence stays attached to the work.
            </p>
            <p className="mx-auto mt-4 max-w-2xl text-sm text-white/55">
              If the product vocabulary is new to you, start here →{" "}
              <Link href="/docs/orientation/vocabulary" className="font-semibold text-aqua underline-offset-2 hover:underline">
                /docs/orientation/vocabulary
              </Link>
              .
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <Link href="/beta" className="btn-primary inline-flex">
                Plan a workspace
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
              The delivery problem Ship actually solves
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
              The workspace is open source and built around the same method. Start with a workspace, connect a repo and
              tracker, set policies, seed knowledge, and keep evidence attached to every automated step.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link href="/beta" className="btn-primary inline-flex">
                Get started
              </Link>
              <Link href="/book" className="btn-secondary inline-flex">
                Read the book
              </Link>
              <Link href="/process" className="btn-secondary inline-flex">
                Browse policies
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
    title: "Useful speed without mystery work",
    body: "Agents can take repeatable review work, but humans keep the product decision. The win is work that moves with ownership, policy, and evidence, not a bigger pile of unexplained activity.",
  },
  {
    kicker: "Governance",
    title: "Audit trail is a side-effect, not a project",
    body: "Every important action should point to a tracker item, branch, PR, check, comment, or knowledge article. Security and compliance read the same trail the team uses.",
  },
  {
    kicker: "Risk",
    title: "Boring architecture, no proprietary control plane",
    body: "Versioned policies, portable integrations, and reviewable repo changes keep vendors replaceable. The method survives a tool migration because the story is yours.",
  },
];
