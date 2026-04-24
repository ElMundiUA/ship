import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "The Ship kit — patterns, collections, tools",
  description:
    "One hub for the three kinds of versioned artifacts Ship distributes: patterns (org playbooks), collections (starter bundles), and tools (integrations). Browseable on the site, fetchable with shipctl.",
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
    href: "/patterns",
    kicker: "Plays catalog",
    title: "Patterns",
    body: "Versioned markdown procedures — PR self-review, release cuts, knowledge refreshes, scheduled cloud roles. The operator console renders each one as a Play your team picks from a menu and assigns as an Automation.",
    accentText: "text-aqua",
    accentBorderHover: "hover:border-aqua/40",
    accentDot: "bg-aqua",
  },
  {
    href: "/collections",
    kicker: "Starter bundles",
    title: "Collections",
    body: "Curated stacks for common product shapes — presets like web-app, api-backend, mobile-app — plus the per-agent rule sets shipctl installs at the right paths on init.",
    accentText: "text-sun",
    accentBorderHover: "hover:border-sun/40",
    accentDot: "bg-sun",
  },
  {
    href: "/tools",
    kicker: "Integrations",
    title: "Tools",
    body: "Tracker, CI, language, and agent adapters — declarative, versioned, security-reviewable. Spell out who plugs into what so platform teams can review once and forget.",
    accentText: "text-coral",
    accentBorderHover: "hover:border-coral/40",
    accentDot: "bg-coral",
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
            <p className="text-sm font-bold uppercase tracking-widest text-aqua">The kit</p>
            <h1 className="font-display mt-4 text-4xl font-bold leading-[1.05] tracking-tight text-white sm:text-5xl lg:text-6xl">
              Everything inside the box —{" "}
              <span className="bg-gradient-to-r from-aqua via-lilac to-coral bg-clip-text text-transparent">
                browseable
              </span>{" "}
              and versioned.
            </h1>
            <p className="mt-6 max-w-2xl text-lg text-white/70">
              Ship distributes four kinds of versioned artifacts. They share one repository, one manifest, and one CLI —
              so the story buyers read on this site and the wiring operators run in their terminal can never silently
              diverge.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="#kinds" className="btn-primary inline-flex">
                Browse the four kinds
              </Link>
              <Link href="/cli" className="btn-secondary inline-flex">
                shipctl reference →
              </Link>
              <Link href="/docs/concepts" className="btn-secondary inline-flex">
                What is an artifact? →
              </Link>
            </div>
          </div>
        </section>

        {/* Four kinds */}
        <section id="kinds" className="py-16 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <div className="mb-10 flex items-baseline justify-between">
              <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">The four artifact kinds</h2>
              <span className="hidden text-xs font-semibold uppercase tracking-[0.2em] text-white/35 sm:inline">
                Browseable on this site · fetchable with <code className="font-mono text-aqua/85">shipctl</code>
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

        {/* How to consume */}
        <section className="border-t border-white/10 bg-black/30 py-16 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.22em] text-white/45">One reader for every kind</p>
            <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">
              <code className="font-mono text-aqua/95">shipctl</code> is the only thing your repo installs.
            </h2>
            <p className="mt-4 max-w-2xl text-base text-white/65">
              The catalog you browse on this site is the same manifest the CLI reads. One subcommand per kind, the same
              flags everywhere, the same provenance string in every PR.
            </p>
            <div className="mt-10 grid gap-4 sm:grid-cols-2">
              {[
                {
                  cmd: "shipctl pattern list",
                  body: "Browse the patterns catalog from the terminal. Pair with shipctl search for fuzzy matching.",
                },
                {
                  cmd: "shipctl pattern show <id>",
                  body: "Print one pattern body to stdout. Fetch the rendered file with shipctl pattern fetch <id>.",
                },
                {
                  cmd: "shipctl collection list",
                  body: "List presets, addendums, and per-agent rule sets. shipctl init --copy-rules installs them.",
                },
                {
                  cmd: "shipctl tool show <id>",
                  body: "Inspect an integration adapter — tracker, CI, language, or agent — before wiring it into your config.",
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
              <Link href="/cli" className="btn-primary inline-flex">
                Full CLI reference
              </Link>
              <Link href="/getting-started" className="btn-secondary inline-flex">
                Setup wizard →
              </Link>
              <Link href="/docs/authoring" className="btn-secondary inline-flex">
                Author your own artifact →
              </Link>
            </div>
          </div>
        </section>

        {/* Where the kit fits in the rest of the site */}
        <section className="py-16 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.22em] text-white/45">Looking for something else?</p>
            <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">Not in the kit</h2>
            <p className="mt-3 max-w-2xl text-sm text-white/65">
              The kit is the catalog. The narrative, the operator&rsquo;s reference, and the field proof live elsewhere on this
              site so the four pages above stay focused on browsing.
            </p>
            <ul className="mt-6 grid gap-2 text-sm text-white/75 sm:grid-cols-2">
              {[
                { href: "/use-cases", label: "Reference deployments & customer stories" },
                { href: "/docs", label: "Operator's reference (concepts, configuration, operating)" },
                { href: "/cli", label: "shipctl CLI reference" },
                { href: "/book", label: "The book — long-form rationale" },
                { href: "/getting-started", label: "Interactive setup wizard" },
                { href: "/docs/authoring", label: "How to author a new artifact" },
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
