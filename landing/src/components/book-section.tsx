import Link from "next/link";

export function BookSection() {
  return (
    <section id="book" className="py-20 sm:py-28">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <div className="grid items-center gap-12 lg:grid-cols-2">
          <div>
            <p className="text-sm font-bold uppercase tracking-widest text-sun">The book</p>
            <h2 className="font-display mt-3 text-3xl font-bold leading-tight text-white sm:text-4xl">
              Long-form rationale that survives the hype cycle
            </h2>
            <p className="mt-5 text-lg text-white/70">
              Getting started gets the workspace moving. The book explains why the fences exist — duplicate pull requests,
              label drift, preview habits, and how saying no protects capacity. Product owners, engineering leads, platform,
              and security should be able to read the same narrative.
            </p>
            <ul className="mt-8 space-y-3 text-sm text-white/75">
              <li className="flex gap-3">
                <span className="text-aqua">✦</span> Narrative glue for product owners, engineering managers, and platform teams.
              </li>
              <li className="flex gap-3">
                <span className="text-aqua">✦</span> Shared vocabulary for audits, retros, and vendor reviews.
              </li>
              <li className="flex gap-3">
                <span className="text-aqua">✦</span> Pairs cleanly with the operational docs — no duplication trap.
              </li>
            </ul>
            <Link href="/book" className="btn-primary mt-10 inline-flex">
              Read the book
            </Link>
          </div>
          <div className="flex flex-col gap-5">
            <div className="glass-panel relative overflow-hidden p-8 sm:p-10">
              <div className="absolute inset-0 bg-gradient-to-br from-lilac/20 via-transparent to-aqua/15" />
              <blockquote className="relative font-display text-2xl font-semibold leading-snug text-white sm:text-3xl">
                &ldquo;Operations live in the mean. The flashy demo celebrates the exception.&rdquo;
              </blockquote>
              <p className="relative mt-6 text-sm text-white/55">
                That is the tone we protect when we say Ship is a product workspace with a methodology underneath — not a
                script dump.
              </p>
              <div className="relative mt-8 flex flex-wrap gap-3 text-xs font-semibold uppercase tracking-wide text-white/45">
                <span className="rounded-full border border-white/15 px-3 py-1">Traceability</span>
                <span className="rounded-full border border-white/15 px-3 py-1">Governance</span>
                <span className="rounded-full border border-white/15 px-3 py-1">Agent economics</span>
              </div>
            </div>
            {/* Ship Log funnel card — quiet companion to the loud book tile. */}
            <Link
              href="/blog"
              className="group relative overflow-hidden rounded-3xl border border-white/10 bg-white/[0.03] p-6 shadow-card transition hover:border-white/25 hover:bg-white/[0.05] sm:p-8"
            >
              <div className="flex items-start gap-4">
                <div className="mt-1 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-sun/40 bg-sun/10 text-sun">
                  <span className="font-display text-sm font-bold">§</span>
                </div>
                <div>
                  <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-sun">Ship Log</p>
                  <p className="mt-2 font-display text-lg font-bold leading-snug text-white transition group-hover:text-aqua sm:text-xl">
                    We don&rsquo;t teach Ship. We run Ship in public.
                  </p>
                  <p className="mt-2 text-sm leading-relaxed text-white/60">
                    Field notes from the repo — autopsies, deletions we&rsquo;re proud of, numbers
                    pulled straight from <code className="text-aqua/85">git log</code>.
                  </p>
                </div>
              </div>
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
