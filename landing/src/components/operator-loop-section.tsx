import Link from "next/link";

const nouns = [
  {
    title: "Workspace",
    blurb:
      "The owner, repos, policies, and integrations that define where Ship is allowed to observe and assist.",
    href: "/beta",
    cta: "Plan the setup",
    accent: "aqua",
  },
  {
    title: "Inbox",
    blurb:
      "Clarifications, improvements, approvals, and failures that need an owner instead of another hidden chat thread.",
    href: "/docs/inbox/overview",
    cta: "How decisions flow",
    accent: "lilac",
  },
  {
    title: "Knowledge",
    blurb:
      "Repo facts, product context, policies, and standing rules that agents can use without guessing from stale Slack threads.",
    href: "/docs/knowledge/overview",
    cta: "Seed knowledge",
    accent: "sun",
  },
  {
    title: "Evidence",
    blurb:
      "A readable trail across tickets, pull requests, checks, comments, and dashboard summaries so reviews do not rely on memory.",
    href: "/docs/operating/morning-loop",
    cta: "Operate the trail",
    accent: "coral",
  },
];

const accentRing = {
  aqua: "border-aqua/30 hover:border-aqua/60 hover:shadow-glow",
  lilac: "border-lilac/30 hover:border-lilac/60",
  sun: "border-sun/30 hover:border-sun/60",
  coral: "border-coral/30 hover:border-coral/60",
} as const;

const accentText = {
  aqua: "text-aqua",
  lilac: "text-lilac",
  sun: "text-sun",
  coral: "text-coral",
} as const;

export function OperatorLoopSection() {
  return (
    <section id="operator-loop" className="border-y border-white/10 bg-gradient-to-br from-aqua/[0.06] via-transparent to-coral/[0.05] py-20 sm:py-24">
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">
          Product delivery loop · four surfaces
        </p>
        <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
          The console is built around ownership, not internal jargon.
        </h2>
        <p className="mt-4 max-w-3xl text-lg text-white/65">
          Solo founders and product owners need to know whether work can safely move, not which internal table wrote the
          latest row. Ship keeps the visible loop small: set policies, review the Inbox, maintain knowledge, and follow
          the evidence.
        </p>

        <div className="mt-10 grid gap-5 items-stretch sm:grid-cols-2 lg:grid-cols-4">
          {nouns.map((n) => (
            <Link
              key={n.title}
              href={n.href}
              className={`group h-full flex flex-col rounded-2xl border bg-gradient-to-br from-white/[0.06] to-transparent p-6 shadow-card transition ${accentRing[n.accent as keyof typeof accentRing]}`}
            >
              <p className={`text-xs font-bold uppercase tracking-widest ${accentText[n.accent as keyof typeof accentText]}`}>
                Surface
              </p>
              <h3 className="font-display mt-2 text-2xl font-bold text-white">{n.title}</h3>
              <p className="mt-3 flex-1 text-sm leading-relaxed text-white/65">{n.blurb}</p>
              <span className="mt-auto text-xs font-semibold text-aqua group-hover:text-white">
                {n.cta} →
              </span>
            </Link>
          ))}
        </div>

        <div className="mt-10 grid gap-5 lg:grid-cols-3">
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-6 sm:p-7 lg:col-span-2">
            <p className="text-xs font-bold uppercase tracking-widest text-white/40">
              Method at a glance
            </p>
            <p className="mt-2 font-display text-lg font-semibold text-white">
              Product speaks decisions. Engineering keeps the contracts versioned.
            </p>
            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <div>
                <p className="font-mono text-[11px] uppercase tracking-widest text-aqua/80">Product owner view</p>
                <ul className="mt-2 space-y-1.5 text-sm text-white/65">
                  <li>What is blocked?</li>
                  <li>What shipped?</li>
                  <li>Who needs to decide?</li>
                  <li>What evidence backs the work?</li>
                </ul>
              </div>
              <div>
                <p className="font-mono text-[11px] uppercase tracking-widest text-coral/80">Engineering contract</p>
                <ul className="mt-2 space-y-1.5 text-sm text-white/65">
                  <li>Versioned prompts and rules</li>
                  <li>Policies for what agents may do</li>
                  <li>Repo and tracker bindings</li>
                  <li>Pull requests with traceable context</li>
                </ul>
              </div>
            </div>
            <p className="mt-5 text-xs text-white/45">
              The technical protocol still exists for teams that need local control. The public product language starts
              from decisions, evidence, and ownership.
            </p>
          </div>

          <div className="rounded-2xl border border-aqua/25 bg-gradient-to-br from-aqua/[0.08] to-transparent p-6 sm:p-7">
            <p className="text-xs font-bold uppercase tracking-widest text-aqua/90">
              Guided workspace
            </p>
            <h3 className="font-display mt-2 text-xl font-bold text-white">
              Ask the system what needs attention
            </h3>
            <p className="mt-3 text-sm leading-relaxed text-white/65">
              The console can answer from connected repos, tracker data, knowledge, and inbox items. Members can inspect;
              admins can change configuration and close decisions.
            </p>
            <Link
              href="/docs/orientation/what-is-ship"
              className="mt-5 inline-flex items-center text-xs font-semibold text-sun hover:text-white"
            >
              How the workspace assistant works →
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
