import Link from "next/link";

const nouns = [
  {
    title: "Plays",
    blurb:
      "Versioned operational procedures from the catalog this site publishes — PR self-review, release cut, knowledge refresh, dependency upgrade. A Play is what the catalog calls a pattern; it is what your team picks from a menu.",
    href: "/patterns",
    cta: "Browse the catalog",
    accent: "aqua",
  },
  {
    title: "Automations",
    blurb:
      "A Play assigned to a scope (one repo / a fleet) with a trigger (event / schedule / manual). Declared in lanes:; rendered as an Automation row in the console; backed by a thin GitHub Actions wrapper around run-agent.yml.",
    href: "/docs/automations",
    cta: "How Automations work",
    accent: "lilac",
  },
  {
    title: "Runs",
    blurb:
      "Outcome-first execution history. Every dispatch produces a Run; the pattern itself reports a RunSummary via shipctl callback so the row reads \"Reviewed PR · 3 suggestions · 1 fix applied\" — not a green check next to a UUID.",
    href: "/docs/operating",
    cta: "Run lifecycle",
    accent: "sun",
  },
  {
    title: "Inbox",
    blurb:
      "The single attention surface. When a Run needs a human — clarification, approval, escalation — it lands here, routed via CODEOWNERS to the right owner. One disposition closes the loop: accept, reject, snooze, reassign.",
    href: "/docs/concepts#inbox",
    cta: "Inbox + routing",
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
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">
          The operator loop · four nouns
        </p>
        <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
          A console that names the work the way your team does
        </h2>
        <p className="mt-4 max-w-3xl text-lg text-white/65">
          Most agent platforms make you learn their internal vocabulary — pipelines, jobs,
          tasks, queues — before you can ship. Ship picks four nouns and uses them everywhere:
          in the docs, in the CLI help, in the operator console. Pick a Play, run it as an
          Automation, watch the Run, triage the Inbox.
        </p>

        <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {nouns.map((n) => (
            <Link
              key={n.title}
              href={n.href}
              className={`group flex flex-col rounded-2xl border bg-gradient-to-br from-white/[0.06] to-transparent p-6 shadow-card transition ${accentRing[n.accent as keyof typeof accentRing]}`}
            >
              <p className={`text-xs font-bold uppercase tracking-widest ${accentText[n.accent as keyof typeof accentText]}`}>
                Noun
              </p>
              <h3 className="font-display mt-2 text-2xl font-bold text-white">{n.title}</h3>
              <p className="mt-3 flex-1 text-sm leading-relaxed text-white/65">{n.blurb}</p>
              <span className="mt-4 text-xs font-semibold text-aqua group-hover:text-white">
                {n.cta} →
              </span>
            </Link>
          ))}
        </div>

        <div className="mt-10 grid gap-5 lg:grid-cols-3">
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-6 sm:p-7 lg:col-span-2">
            <p className="text-xs font-bold uppercase tracking-widest text-white/40">
              Vocabulary at a glance
            </p>
            <p className="mt-2 font-display text-lg font-semibold text-white">
              Operators speak product. Code speaks protocol. Both are correct.
            </p>
            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <div>
                <p className="font-mono text-[11px] uppercase tracking-widest text-aqua/80">CLI / YAML / API</p>
                <ul className="mt-2 space-y-1.5 text-sm text-white/65">
                  <li>
                    <code className="font-mono text-aqua/90">lanes:</code> in <code className="font-mono text-aqua/90">.ship/config.yml</code>
                  </li>
                  <li>
                    <code className="font-mono text-aqua/90">pattern:</code> artifacts (RFC-0001)
                  </li>
                  <li>
                    <code className="font-mono text-aqua/90">pipeline_runs</code> + <code className="font-mono text-aqua/90">shipctl callback</code>
                  </li>
                  <li>clarifications · improvements · approvals queue</li>
                </ul>
              </div>
              <div>
                <p className="font-mono text-[11px] uppercase tracking-widest text-coral/80">Operator console</p>
                <ul className="mt-2 space-y-1.5 text-sm text-white/65">
                  <li><strong className="text-white/85">Automations</strong></li>
                  <li><strong className="text-white/85">Plays</strong></li>
                  <li><strong className="text-white/85">Runs</strong></li>
                  <li><strong className="text-white/85">Inbox</strong> items</li>
                </ul>
              </div>
            </div>
            <p className="mt-5 text-xs text-white/45">
              The CLI surface (<code className="font-mono text-white/60">lanes:</code>,{" "}
              <code className="font-mono text-white/60">--lane</code>,{" "}
              <code className="font-mono text-white/60">shipctl pattern</code>) is
              protocol-stable and ships indefinitely. Prose reaches for the operator nouns
              because that is what your team will say in standups.
            </p>
          </div>

          <div className="rounded-2xl border border-aqua/25 bg-gradient-to-br from-aqua/[0.08] to-transparent p-6 sm:p-7">
            <p className="text-xs font-bold uppercase tracking-widest text-aqua/90">
              Single chat window
            </p>
            <h3 className="font-display mt-2 text-xl font-bold text-white">
              Navigator: the agent that knows your console
            </h3>
            <p className="mt-3 text-sm leading-relaxed text-white/65">
              Ship&apos;s in-console agent has tools for every surface — list Inbox items,
              dispose them, run a Play, toggle an Automation, query Runs, look at Coverage,
              search the knowledge base. Members can ask, admins can mutate.
            </p>
            <Link
              href="/docs/concepts#navigator"
              className="mt-5 inline-flex items-center text-xs font-semibold text-aqua hover:text-white"
            >
              How Navigator dispatches tools →
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
