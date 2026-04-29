import type { Metadata } from "next";
import Link from "next/link";
import { PatternsCatalog } from "@/components/patterns-catalog";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { loadPatternsManifest } from "@/lib/patterns";

export const metadata: Metadata = {
  title: "Policies and procedures — Ship",
  description:
    "Policies and procedures are reviewable rules for repeatable product and engineering work: review, release, knowledge, audit, and safety checks.",
};

export default function PatternsPage() {
  const manifest = loadPatternsManifest();

  return (
    <>
      <SiteHeader />
      <main>
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_90%_60%_at_50%_-10%,rgba(179,136,255,0.35),transparent_55%)]" />
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_50%_40%_at_100%_20%,rgba(255,213,74,0.12),transparent_50%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-lilac">Policies · procedures</p>
            <h1 className="font-display mt-4 text-4xl font-bold leading-tight text-white sm:text-5xl">
              Policies are how founders keep agents bounded
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              A <strong className="text-white">policy</strong> says what automation may do, what evidence it must leave,
              and when a human must decide. Procedures keep repeatable work like PR review, release checks, knowledge
              refreshes, dependency reviews, and audits readable. New to the product vocabulary?{" "}
              <Link href="/docs/concepts" className="font-semibold text-aqua underline-offset-2 hover:underline">
                Read the concepts →
              </Link>
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <a href="#library" className="btn-primary inline-flex">
                Browse policies
              </a>
              <a href="#how" className="btn-secondary inline-flex">
                How to use
              </a>
            </div>
          </div>
        </section>

        <section className="py-16 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <div className="grid gap-8 lg:grid-cols-3">
              <div className="glass-panel p-6 sm:p-8">
                <p className="text-xs font-bold uppercase tracking-widest text-aqua">Why</p>
                <h2 className="font-display mt-2 text-xl font-bold text-white">Improvements land in your Inbox</h2>
                <p className="mt-3 text-sm leading-relaxed text-white/65">
                  When an agent or reviewer finds a better rule, it should become an improvement with an owner and a PR,
                  not a private prompt tweak. Once merged, every workspace can read the same policy.
                </p>
              </div>
              <div className="glass-panel p-6 sm:p-8">
                <p className="text-xs font-bold uppercase tracking-widest text-sun">Neutral shape</p>
                <h2 className="font-display mt-2 text-xl font-bold text-white">Runtime-agnostic</h2>
                <p className="mt-3 text-sm leading-relaxed text-white/65">
                  Policies are plain text on purpose: a solo founder can read them, a product owner can approve them,
                  and an engineer can review changes in a PR.
                </p>
              </div>
              <div className="glass-panel p-6 sm:p-8">
                <p className="text-xs font-bold uppercase tracking-widest text-lilac">Discovery</p>
                <h2 className="font-display mt-2 text-xl font-bold text-white">Evidence-first</h2>
                <p className="mt-3 text-sm leading-relaxed text-white/65">
                  A useful policy names the proof: ticket, PR, check, comment, or Inbox decision. Without evidence, the
                  automation did not finish the work.
                </p>
              </div>
            </div>
          </div>
        </section>

        <section id="how" className="border-y border-white/10 bg-black/25 py-16 sm:py-20">
          <div className="mx-auto max-w-3xl px-4 sm:px-6">
            <h2 className="font-display text-center text-3xl font-bold text-white sm:text-4xl">How owners use this</h2>
            <ol className="mt-12 space-y-8 text-white/75">
              <li className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-aqua/30 bg-aqua/10 font-display font-bold text-aqua">
                  1
                </span>
                <div>
                  <p className="font-display font-semibold text-white">Set the boundary first</p>
                  <p className="mt-1 text-sm leading-relaxed">
                    Decide which repo, tracker state, file area, or automation path is allowed before any agent starts
                    moving work.
                  </p>
                </div>
              </li>
              <li className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-lilac/30 bg-lilac/10 font-display font-bold text-lilac">
                  2
                </span>
                <div>
                  <p className="font-display font-semibold text-white">Name the evidence</p>
                  <p className="mt-1 text-sm leading-relaxed">
                    Require the ticket, PR, check, comment, or Inbox decision that proves the work is safe to review.
                  </p>
                </div>
              </li>
              <li className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-sun/30 bg-sun/10 font-display font-bold text-sun">
                  3
                </span>
                <div>
                  <p className="font-display font-semibold text-white">Change policies through review</p>
                  <p className="mt-1 text-sm leading-relaxed">
                    Agents may propose edits; humans approve. That keeps the policy set{" "}
                    <strong className="text-white/90">moderated</strong> without banning automation from the author seat.
                  </p>
                </div>
              </li>
            </ol>
            <p className="mt-12 text-center text-sm text-white/50">
              Keep policy changes reviewable so product decisions and agent instructions move together.
            </p>
          </div>
        </section>

        <section id="library" className="py-16 sm:py-24">
          <div className="mx-auto max-w-6xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-coral">Index</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">Current policy and procedure library</h2>
            <p className="mx-auto mt-4 max-w-2xl text-white/65">
              {manifest.description} Currently <strong className="text-white">{manifest.patterns.length}</strong> entries.
            </p>
          </div>
          <div className="mt-12">
            <PatternsCatalog patterns={manifest.patterns} />
          </div>
        </section>

      </main>
      <SiteFooter />
    </>
  );
}
