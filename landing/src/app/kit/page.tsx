import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Ship product surfaces — workspace, policies, knowledge, evidence",
  description:
    "One hub for the owner-facing surfaces Ship uses to keep AI-assisted delivery bounded, explainable, and reviewable.",
};

type Tile = {
  href: string;
  kicker: string;
  title: string;
  body: string;
  /**
   * Static class strings only — Tailwind JIT cannot extract dynamic
   * `hover:border-${color}/40` constructions, so the full classes live
   * here verbatim.
   */
  accentText: string;
  accentBorderHover: string;
  accentDot: string;
};

const TILES: Tile[] = [
  {
    href: "/beta",
    kicker: "Control",
    title: "Workspace",
    body: "Connect the product repo, tracker, owner, members, and integrations before automation starts moving work.",
    accentText: "text-aqua",
    accentBorderHover: "hover:border-aqua/40",
    accentDot: "bg-aqua",
  },
  {
    href: "/process",
    kicker: "Boundaries",
    title: "Policies",
    body: "Standing rules for allowed actions, review gates, required evidence, and the moments where a human must decide.",
    accentText: "text-sun",
    accentBorderHover: "hover:border-sun/40",
    accentDot: "bg-sun",
  },
  {
    href: "/docs/knowledge/overview",
    kicker: "Context",
    title: "Knowledge",
    body: "Repo facts, product constraints, customer context, and review notes agents can use without stale chat archaeology.",
    accentText: "text-coral",
    accentBorderHover: "hover:border-coral/40",
    accentDot: "bg-coral",
  },
  {
    href: "/docs/operating/morning-loop",
    kicker: "Proof",
    title: "Evidence",
    body: "Tickets, pull requests, checks, comments, and Inbox decisions connected into a story a buyer or reviewer can follow.",
    accentText: "text-lilac",
    accentBorderHover: "hover:border-lilac/40",
    accentDot: "bg-lilac",
  },
];

export default function KitPage() {
  return (
    <div className="min-h-screen bg-ink pt-16">
      <SiteHeader />
      <main>
        {/* Hero */}
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-20 sm:pb-20 sm:pt-24">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_90%_60%_at_50%_-10%,rgba(46,230,214,0.18),transparent_55%)]" />
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_50%_40%_at_100%_20%,rgba(179,136,255,0.12),transparent_50%)]" />
          <div className="relative mx-auto max-w-5xl px-4 sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-aqua">Product surfaces</p>
            <h1 className="font-display mt-4 text-4xl font-bold leading-[1.05] tracking-tight text-white sm:text-5xl lg:text-6xl">
              The owner workspace —{" "}
              <span className="bg-gradient-to-r from-aqua via-lilac to-coral bg-clip-text text-transparent">
                bounded
              </span>{" "}
              before agents move.
            </h1>
            <p className="mt-6 max-w-2xl text-lg text-white/70">
              Ship now starts from the product owner view: workspace, policies, knowledge, and evidence. Engineers still
              get reviewable contracts underneath, but the visible product is built for solo founders and owners first.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="#kinds" className="btn-primary inline-flex">
                Browse the surfaces
              </Link>
              <Link href="/docs/orientation/vocabulary" className="btn-secondary inline-flex">
                Product concepts →
              </Link>
            </div>
          </div>
        </section>

        {/* Four surfaces */}
        <section id="kinds" className="py-16 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <div className="mb-10 flex items-baseline justify-between">
              <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">The workspace surfaces</h2>
              <span className="hidden text-xs font-semibold uppercase tracking-[0.2em] text-white/35 sm:inline">
                Owner-readable · engineer-auditable
              </span>
            </div>
            <div className="grid gap-5 lg:grid-cols-2">
              {TILES.map((t) => (
                <Link
                  key={t.href}
                  href={t.href}
                  className={`group relative flex flex-col overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.05] via-white/[0.02] to-transparent p-6 shadow-card transition hover:bg-white/[0.04] sm:p-8 ${t.accentBorderHover}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <span className={`inline-flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.22em] ${t.accentText}`}>
                        <span className={`h-1.5 w-1.5 rounded-full ${t.accentDot}`} aria-hidden />
                        {t.kicker}
                      </span>
                      <h3 className="font-display mt-3 text-2xl font-bold text-white sm:text-3xl">{t.title}</h3>
                    </div>
                    <span
                      className={`shrink-0 text-xl font-bold ${t.accentText} translate-x-0 opacity-50 transition group-hover:translate-x-1 group-hover:opacity-100`}
                      aria-hidden
                    >
                      →
                    </span>
                  </div>
                  <p className="mt-4 text-sm leading-relaxed text-white/70 sm:text-base">{t.body}</p>
                  <div className="mt-6 flex items-center gap-3 text-xs text-white/45">
                    <span className="font-mono">{t.href}</span>
                    <span className="h-px flex-1 bg-white/10" aria-hidden />
                    <span className={`font-semibold ${t.accentText}`}>Explore →</span>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        </section>

        {/* How to operate */}
        <section className="border-t border-white/10 bg-black/30 py-16 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.22em] text-white/45">Two words on policies</p>
            <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">
              Policies are the guardrails, not the bureaucracy.
            </h2>
            <p className="mt-4 max-w-2xl text-base text-white/65">
              A policy says what an agent may do, what evidence it must leave, and when it must stop for a human. That is
              how Ship keeps founder speed without turning the repo into an unsupervised experiment.
            </p>
            <div className="mt-10 grid gap-4 sm:grid-cols-2">
              {[
                {
                  cmd: "Allowed scope",
                  body: "Which repos, tracker states, files, and automations are in bounds for this workspace.",
                },
                {
                  cmd: "Review gates",
                  body: "Which changes require owner approval, code review, test evidence, or a release note before they move.",
                },
                {
                  cmd: "Evidence rules",
                  body: "Which ticket, PR, check, comment, or Inbox decision must exist before work can be considered done.",
                },
                {
                  cmd: "Escalation",
                  body: "The moments where automation stops and asks the founder, product owner, or reviewer to decide.",
                },
              ].map((row) => (
                <div
                  key={row.cmd}
                  className="rounded-2xl border border-white/10 bg-white/[0.03] p-5"
                >
                  <code className="block font-mono text-sm font-semibold text-aqua/95">{row.cmd}</code>
                  <p className="mt-3 text-sm leading-relaxed text-white/65">{row.body}</p>
                </div>
              ))}
            </div>
            <div className="mt-10 flex flex-wrap gap-3">
              <Link href="/beta" className="btn-secondary inline-flex">
                Workspace setup →
              </Link>
              <Link href="/process" className="btn-primary inline-flex">
                Policy library →
              </Link>
            </div>
          </div>
        </section>

        {/* Where the product surfaces fit in the rest of the site */}
        <section className="py-16 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.22em] text-white/45">Looking for something else?</p>
            <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">Related surfaces</h2>
            <p className="mt-3 max-w-2xl text-sm text-white/65">
              The product surfaces above are the public entry point. Longer rationale, docs, and field proof live
              elsewhere on this site.
            </p>
            <ul className="mt-6 grid gap-2 text-sm text-white/75 sm:grid-cols-2">
              {[
                { href: "/use-cases", label: "Reference deployments & customer stories" },
                { href: "/docs", label: "Product docs and technical reference" },
                { href: "/book", label: "The book — long-form rationale" },
                { href: "/beta", label: "Workspace setup guide" },
                { href: "/docs/process/overview", label: "Bounded automations" },
              ].map((row) => (
                <li key={row.href}>
                  <Link
                    href={row.href}
                    className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-black/20 px-3.5 py-2.5 transition hover:border-aqua/35 hover:bg-white/[0.04]"
                  >
                    <span>{row.label}</span>
                    <span className="text-xs text-aqua">→</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
