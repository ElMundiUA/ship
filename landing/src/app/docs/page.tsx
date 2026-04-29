import type { Metadata } from "next";
import Link from "next/link";
import { ACCENT_HOVER_BORDER, ACCENT_TEXT, DOCS_NAV } from "@/lib/docs-nav";

export const metadata: Metadata = {
  title: "Ship docs — product workspace guide",
  description:
    "Docs for setting up and operating Ship workspaces: repos, trackers, policies, knowledge, Inbox, automations, and evidence.",
};

export default function DocsHomePage() {
  return (
    <main className="docs-page">
      {/* Hero */}
      <section className="docs-hero">
        <p className="docs-hero-kicker">Get started with Ship</p>
        <h1 className="docs-hero-title">
          Connect the product workspace, then keep the{" "}
          <span className="bg-gradient-to-r from-aqua via-lilac to-coral bg-clip-text text-transparent">
            evidence trail
          </span>
          {" "}readable.
        </h1>
        <p className="docs-hero-lede">
          Start with a workspace, connected repos, tracker binding, policies, knowledge, and an Inbox for decisions. The
          first path is about founder ownership, product decisions, and evidence.
        </p>
        <div className="mt-7 flex flex-wrap gap-3">
          <Link href="/getting-started" className="btn-primary inline-flex">
            Plan workspace setup
          </Link>
          <Link href="/docs/concepts" className="btn-secondary inline-flex">
            Browse product concepts
          </Link>
        </div>
      </section>

      {/* Section grid grouped by purpose, in lockstep with the sidebar */}
      <section aria-label="Docs sections" className="space-y-12">
        {DOCS_NAV.map((group) => (
          <div key={group.label}>
            <div className="mb-5 flex items-baseline justify-between">
              <h2 className={`font-display text-xs font-bold uppercase tracking-[0.22em] ${ACCENT_TEXT[group.accent]}`}>
                {group.label}
              </h2>
              <span className="text-[10px] uppercase tracking-widest text-white/30">
                {group.items.length} {group.items.length === 1 ? "page" : "pages"}
              </span>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              {group.items.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`group relative overflow-hidden rounded-2xl border border-white/10 bg-white/[0.025] p-5 transition hover:bg-white/[0.05] sm:p-6 ${ACCENT_HOVER_BORDER[group.accent]}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="font-display text-lg font-bold text-white sm:text-xl">
                      {item.label}
                    </h3>
                    <span
                      className={`shrink-0 text-base ${ACCENT_TEXT[group.accent]} opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100`}
                      aria-hidden
                    >
                      →
                    </span>
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-white/65">
                    {item.blurb}
                  </p>
                </Link>
              ))}
            </div>
          </div>
        ))}
      </section>

      {/* What you will *not* find here */}
      <section className="mt-16 rounded-3xl border border-white/10 bg-gradient-to-br from-white/[0.04] via-white/[0.02] to-transparent p-6 sm:p-8">
        <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/45">
          Looking for something else?
        </p>
        <h2 className="font-display mt-2 text-2xl font-bold text-white">
          Not in the Docs
        </h2>
        <p className="mt-3 max-w-2xl text-sm text-white/65">
          Some surfaces live outside this manual so founders, product owners, platform teams, and engineers can enter at
          the right altitude.
        </p>
        <ul className="mt-6 grid gap-2 text-sm text-white/75 sm:grid-cols-2">
          {[
            { href: "/kit", label: "Product surfaces — workspace, policies, knowledge, evidence" },
            { href: "/use-cases", label: "Customer stories & reference deployments" },
            { href: "/book", label: "Long-form rationale (the book)" },
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
      </section>
    </main>
  );
}
