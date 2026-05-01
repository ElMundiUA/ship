import Link from "next/link";

/**
 * "Ship ships Ship" — proof block from our own dogfooding.
 *
 * The headline claim: this entire workspace and product were written
 * with zero hand-typed lines of human code. Every commit on Ship's main
 * branch since extraction (2026-04-07) was authored by AI agents under
 * Ship's process model — Cursor, Claude Code, Codex — with humans
 * reviewing, deciding priority, and merging.
 *
 * If the numbers below get stale, regenerate from the repo:
 *   - Lines of code: cloc-style sweep over backend/, console/, landing/,
 *     cli/, e2e/, documentation/ — excluding node_modules/.venv/dist.
 *   - Commits: `git log --oneline | wc -l`.
 *   - Days: subtract the first commit date from today.
 *   - Authors: `git shortlog -sn --all`.
 */

type Stat = {
  value: string;
  unit?: string;
  label: string;
  body: string;
  accent: "aqua" | "sun" | "lilac" | "coral";
};

const STATS: Stat[] = [
  {
    value: "0",
    label: "Lines hand-typed by humans",
    body: "Every line of code in production was written by an AI executor — Cursor, Claude Code, Codex. Humans set policy, picked priority, and reviewed PRs.",
    accent: "aqua",
  },
  {
    value: "234",
    unit: "k+",
    label: "Lines of code shipped",
    body: "119k Python (backend, CLI), 79k TypeScript (console + landing + e2e), 36k Markdown (docs, blog, book). All of it AI-authored.",
    accent: "sun",
  },
  {
    value: "501",
    label: "Commits in 25 days",
    body: "From extraction on 2026-04-07 to today. Every commit on main, public, with the AI executor named in the trail.",
    accent: "lilac",
  },
  {
    value: "7",
    label: "Team members, none typing code",
    body: "Leadership, engineering, advisors — the whole team reviews diffs, names routines, sets policy. Not a typing pool. The product proves the workflow scales.",
    accent: "coral",
  },
];

export function DogfoodSection() {
  return (
    <section className="relative overflow-hidden border-y border-white/10 bg-gradient-to-br from-aqua/[0.05] via-black/40 to-sun/[0.04] py-20 sm:py-28">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_50%_-10%,rgba(207,169,107,0.10),transparent_60%)]" />
      <div className="relative mx-auto max-w-[88rem] px-4 sm:px-6">
        <div className="mx-auto max-w-3xl text-center">
          <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Ship ships Ship</p>
          <h2 className="font-display mt-3 text-4xl font-bold text-white sm:text-5xl lg:text-[3.25rem] lg:leading-[1.04]">
            Zero lines of human-typed code.
          </h2>
          <p className="mt-6 text-base leading-relaxed text-white/70 sm:text-lg">
            This entire workspace — backend, console, landing, CLI, docs, blog — was written by AI agents under Ship&apos;s
            own process. Humans reviewed, decided, merged. No one typed code by hand. The product is the proof of
            the workflow.
          </p>
        </div>

        <div className="mt-14 grid items-stretch gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {STATS.map((stat) => (
            <StatCard key={stat.label} stat={stat} />
          ))}
        </div>

        <div className="mt-14 grid items-stretch gap-6 lg:grid-cols-3">
          <Quote
            kicker="From the blog"
            line="189 commits. 16 days. One repo."
            cite="ship-the-first-two-weeks"
          />
          <Quote
            kicker="From the catalogue"
            line="15 specialists, 8 routines, 1 production process — running on themselves."
            cite="catalogue v0.14.2"
          />
          <Quote
            kicker="From the changelog"
            line="Phase 8 → 10 shipped in three weeks. Same workspace built it."
            cite="phases 8–10"
          />
        </div>

        <div className="mt-14 flex flex-wrap justify-center gap-3">
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
      <div className="flex items-baseline gap-1">
        <p className={`font-display text-[3.5rem] font-bold leading-none ${ACCENT_VALUE[stat.accent]} sm:text-6xl`}>
          {stat.value}
        </p>
        {stat.unit && (
          <p className={`font-display text-2xl font-bold ${ACCENT_VALUE[stat.accent]} sm:text-3xl`}>{stat.unit}</p>
        )}
      </div>
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
