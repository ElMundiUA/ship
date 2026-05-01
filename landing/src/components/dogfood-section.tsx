import Link from "next/link";

/**
 * "Ship building Ship" — proof block from our own dogfooding.
 *
 * Numbers come from the Ship repo's git history and blog index, not
 * from a customer reference deployment. The pitch is: we run on the
 * same workspace we sell, and these are the numbers the workspace
 * produced shipping itself in 25 days.
 *
 * If the numbers below get stale, regenerate from `git log` and the
 * blog folder before next major edit.
 */

type Stat = {
  value: string;
  label: string;
  body: string;
  accent: "aqua" | "sun" | "lilac" | "coral";
};

const STATS: Stat[] = [
  {
    value: "417",
    label: "Commits in 25 days",
    body: "Every commit on Ship's main branch since 2026-04-07. Public history, not a curated metric.",
    accent: "aqua",
  },
  {
    value: "8.9%",
    label: "Authored by routines",
    body: "37 commits landed without a human at the keyboard — Cursor agent and the ship-elmundi[bot] running routines on schedule.",
    accent: "sun",
  },
  {
    value: "11",
    label: "ELS tickets shipped",
    body: "Knowledge ingestion (KB-1 → KB-6), navigator (ELS-52 → ELS-54), routing reform (ELS-32 → ELS-40). Real tickets, real flow.",
    accent: "lilac",
  },
  {
    value: "18",
    label: "Blog posts in the same window",
    body: "Marketing operations on the same workspace — drafts, reviews, publishes, tied to the same audit log as the code.",
    accent: "coral",
  },
];

export function DogfoodSection() {
  return (
    <section className="relative overflow-hidden border-y border-white/10 bg-gradient-to-br from-aqua/[0.05] via-black/40 to-sun/[0.04] py-20 sm:py-28">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_50%_-10%,rgba(46,230,214,0.08),transparent_60%)]" />
      <div className="relative mx-auto max-w-[88rem] px-4 sm:px-6">
        <div className="mx-auto max-w-3xl text-center">
          <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Ship ships Ship</p>
          <h2 className="font-display mt-3 text-3xl font-bold text-white sm:text-4xl lg:text-[2.75rem]">
            We run on the workspace we sell.
          </h2>
          <p className="mt-5 text-base leading-relaxed text-white/70 sm:text-lg">
            The numbers below come from Ship&apos;s own repository — same processes, same specialists, same routines you
            get when you spin up a workspace. Twenty-five days, four humans, two agents, one production-depth process.
          </p>
        </div>

        <div className="mt-14 grid items-stretch gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {STATS.map((stat) => (
            <StatCard key={stat.label} stat={stat} />
          ))}
        </div>

        <div className="mt-12 grid items-stretch gap-6 lg:grid-cols-3">
          <Quote
            kicker="From the blog"
            line="189 commits. 16 days. One repo."
            cite="ship-the-first-two-weeks"
          />
          <Quote
            kicker="From the changelog"
            line="Phase 8 → 10 shipped in three weeks."
            cite="phases 8–10"
          />
          <Quote
            kicker="From git log"
            line="ship-elmundi[bot] · 32 commits"
            cite="routine-driven automation"
          />
        </div>

        <div className="mt-12 flex flex-wrap justify-center gap-3">
          <Link href="/blog" className="btn-secondary inline-flex">
            Read the blog
          </Link>
          <a
            href="https://github.com/ElMundiUA/ship/commits/main"
            target="_blank"
            rel="noreferrer"
            className="btn-ghost inline-flex"
          >
            See the commit history
          </a>
        </div>
      </div>
    </section>
  );
}

const ACCENT_VALUE: Record<Stat["accent"], string> = {
  aqua: "text-aqua",
  sun: "text-sun",
  lilac: "text-lilac",
  coral: "text-coral",
};

const ACCENT_BORDER: Record<Stat["accent"], string> = {
  aqua: "border-aqua/25",
  sun: "border-sun/25",
  lilac: "border-lilac/25",
  coral: "border-coral/25",
};

function StatCard({ stat }: { stat: Stat }) {
  return (
    <div
      className={`flex h-full flex-col rounded-2xl border ${ACCENT_BORDER[stat.accent]} bg-gradient-to-br from-white/[0.04] via-white/[0.015] to-transparent p-6 shadow-card`}
    >
      <p className={`font-display text-[2.75rem] font-bold leading-none ${ACCENT_VALUE[stat.accent]} sm:text-5xl`}>
        {stat.value}
      </p>
      <p className="font-display mt-3 text-sm font-bold uppercase tracking-[0.14em] text-white/85">
        {stat.label}
      </p>
      <p className="mt-3 text-xs leading-relaxed text-white/55 sm:text-sm">{stat.body}</p>
    </div>
  );
}

function Quote({ kicker, line, cite }: { kicker: string; line: string; cite: string }) {
  return (
    <div className="flex h-full flex-col rounded-2xl border border-white/10 bg-white/[0.02] p-5">
      <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-white/45">{kicker}</p>
      <p className="font-display mt-3 text-lg font-bold text-white sm:text-xl">{line}</p>
      <p className="mt-auto pt-4 text-[11px] text-white/40">— {cite}</p>
    </div>
  );
}
