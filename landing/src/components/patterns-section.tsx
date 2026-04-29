import Link from "next/link";

export function PatternsSection() {
  return (
    <section id="patterns" className="border-y border-white/10 bg-gradient-to-br from-lilac/[0.08] via-transparent to-coral/[0.06] py-20 sm:py-28">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <div className="grid items-center gap-12 lg:grid-cols-2">
          <div className="glass-panel relative order-2 overflow-hidden p-8 sm:p-10 lg:order-1">
            <div className="absolute inset-0 bg-gradient-to-br from-aqua/10 via-transparent to-lilac/10" />
            <p className="relative text-xs font-bold uppercase tracking-widest text-white/40">Library</p>
            <p className="relative mt-4 font-display text-2xl font-semibold leading-snug text-white sm:text-3xl">
              Policies and procedures are reviewable
            </p>
            <ul className="relative mt-6 space-y-3 text-sm leading-relaxed text-white/65">
              <li className="flex gap-2">
                <span className="text-aqua">✦</span>
                <span>
                  <strong className="text-white/85">Policies</strong> — short boundaries for allowed repos, review gates,
                  evidence requirements, and when an owner must decide.
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-aqua">✦</span>
                <span>
                  <strong className="text-white/85">Procedures</strong> — repeatable review paths for release checks,
                  knowledge refreshes, dependency reviews, and audits.
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-aqua">✦</span>
                <span>
                  <strong className="text-white/85">Evidence</strong> — every procedure should leave a ticket, PR, check,
                  comment, or Inbox decision that a non-engineer can inspect.
                </span>
              </li>
            </ul>
            <p className="relative mt-6 text-xs text-white/45">
              Pull requests stay the moderation gate: nothing ships to the library until your reviewers agree.
            </p>
          </div>
          <div className="order-1 lg:order-2">
            <p className="text-sm font-bold uppercase tracking-widest text-lilac">Reviewable content</p>
            <h2 className="font-display mt-3 text-3xl font-bold leading-tight text-white sm:text-4xl">
              Rules a solo founder can explain and a team can audit
            </h2>
            <p className="mt-5 text-lg text-white/70">
              The goal is legibility: a product owner can understand the boundary before automation starts, and engineers
              can keep the same rule versioned beside the work.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/patterns" className="btn-primary inline-flex">
                Browse policies
              </Link>
              <Link href="/docs/automations" className="btn-secondary inline-flex">
                Bounded automation
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
