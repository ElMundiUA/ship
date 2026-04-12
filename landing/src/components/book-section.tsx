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
              Getting started gets you moving. The book explains why the fences exist — duplicate pull requests, label drift,
              preview habits, and how saying no protects capacity. It is linked from the top of every page so sponsors can
              read the same narrative engineers reference.
            </p>
            <ul className="mt-8 space-y-3 text-sm text-white/75">
              <li className="flex gap-3">
                <span className="text-aqua">✦</span> Narrative glue for engineering managers and agent operators.
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
          <div className="glass-panel relative overflow-hidden p-8 sm:p-10">
            <div className="absolute inset-0 bg-gradient-to-br from-lilac/20 via-transparent to-aqua/15" />
            <blockquote className="relative font-display text-2xl font-semibold leading-snug text-white sm:text-3xl">
              &ldquo;Operations live in the mean. The flashy demo celebrates the exception.&rdquo;
            </blockquote>
            <p className="relative mt-6 text-sm text-white/55">
              That is the tone we protect when we say Ship is a methodology layer — not a script dump.
            </p>
            <div className="relative mt-8 flex flex-wrap gap-3 text-xs font-semibold uppercase tracking-wide text-white/45">
              <span className="rounded-full border border-white/15 px-3 py-1">Traceability</span>
              <span className="rounded-full border border-white/15 px-3 py-1">Governance</span>
              <span className="rounded-full border border-white/15 px-3 py-1">Agent economics</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
