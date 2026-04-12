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
              Patterns sit beside tools, workflows, and collections
            </p>
            <ul className="relative mt-6 space-y-3 text-sm leading-relaxed text-white/65">
              <li className="flex gap-2">
                <span className="text-aqua">✦</span>
                <span>
                  <strong className="text-white/85">Patterns</strong> — short, reviewable plays for onboarding, cloud roles,
                  and SDLC lanes.
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-aqua">✦</span>
                <span>
                  <strong className="text-white/85">Tools &amp; workflows</strong> — who integrates where, and how work is
                  supposed to move — in the same catalog the site lists.
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-aqua">✦</span>
                <span>
                  <strong className="text-white/85">Collections</strong> — bundles for common product shapes so a new team
                  inherits opinion without inheriting mystery.
                </span>
              </li>
            </ul>
            <p className="relative mt-6 text-xs text-white/45">
              Pull requests stay the moderation gate: nothing ships to the library until your reviewers agree.
            </p>
          </div>
          <div className="order-1 lg:order-2">
            <p className="text-sm font-bold uppercase tracking-widest text-lilac">Operating content</p>
            <h2 className="font-display mt-3 text-3xl font-bold leading-tight text-white sm:text-4xl">
              Playbooks your teams can discover — not email attachments
            </h2>
            <p className="mt-5 text-lg text-white/70">
              The goal is legibility: a program manager can browse tools and workflows while an engineer pulls the same
              pattern text into an agent run — without a separate “internal wiki” that goes stale.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/patterns" className="btn-primary inline-flex">
                Browse patterns
              </Link>
              <Link href="/tools" className="btn-secondary inline-flex">
                Tools
              </Link>
              <Link href="/workflows" className="btn-secondary inline-flex">
                Workflows
              </Link>
              <Link href="/collections" className="btn-secondary inline-flex">
                Collections
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
