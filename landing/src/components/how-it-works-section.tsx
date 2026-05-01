import Link from "next/link";

const steps = [
  {
    n: "01",
    title: "Create a workspace",
    body: "Start with the founder, product owner, or product area that owns the outcome. Ship keeps that scope visible before any automation runs.",
    code: "Workspace · members · policy",
  },
  {
    n: "02",
    title: "Connect the repo",
    body: "Install the GitHub App, activate the repositories Ship should observe, and let the console detect what is already wired.",
    code: "Repo · GitHub App · bundle",
  },
  {
    n: "03",
    title: "Bind the tracker",
    body: "Tie work back to Linear, GitHub Issues, Jira, or the tracker your team already uses so intent stays human-owned.",
    code: "Tracker · states · owners",
  },
  {
    n: "04",
    title: "Set policies and knowledge",
    body: "Give agents and reviewers the product facts and boundaries they need: brand, code style, tests, review rules, and repo context.",
    code: "Knowledge · policies · secrets",
  },
  {
    n: "05",
    title: "Review decisions",
    body: "The dashboard and Inbox show blockers, clarifications, improvements, shipped work, and the evidence behind each action.",
    code: "Dashboard · Inbox · PR evidence",
  },
];

export function HowItWorksSection() {
  return (
    <section id="how-it-works" className="border-y border-white/10 bg-black/25 py-20 sm:py-24">
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">
          How it works · from workspace to evidence
        </p>
        <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
          One place to see what moved, what stalled, and who decides.
        </h2>
        <p className="mt-4 max-w-3xl text-lg text-white/65">
          Ship is not a promise that agents will choose for you. It is a workspace that lets a solo founder or product
          owner set boundaries, inspect progress, and keep every automated step readable: where the request came from,
          which repo it touched, what evidence it left, and who owns the next decision.
        </p>

        <ol className="mt-12 grid gap-5 md:grid-cols-2 lg:grid-cols-5">
          {steps.map((s) => (
            <li
              key={s.n}
              className="group relative flex flex-col rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.06] to-transparent p-6 shadow-card transition hover:border-aqua/35"
            >
              <div className="flex items-center gap-3">
                <span className="font-display text-2xl font-bold text-aqua/90">{s.n}</span>
                <span className="h-px flex-1 bg-gradient-to-r from-aqua/30 to-transparent" aria-hidden />
                <span className="rounded-full border border-white/10 bg-black/30 px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-white/55">
                  step
                </span>
              </div>
              <h3 className="font-display mt-3 text-xl font-bold text-white">{s.title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-white/65">{s.body}</p>
              <pre className="mt-4 overflow-x-auto rounded-lg border border-white/10 bg-black/50 p-3 font-mono text-[12px] leading-relaxed text-aqua/90">
{s.code}
              </pre>
            </li>
          ))}
        </ol>

        <div className="mt-10 flex flex-wrap items-center gap-3 text-sm">
          <Link
            className="inline-flex items-center rounded-full border border-aqua/30 bg-aqua/[0.08] px-4 py-1.5 font-semibold text-aqua hover:border-aqua/60"
            href="/beta"
          >
            Start with the workspace guide
          </Link>
          <Link
            className="inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-4 py-1.5 font-semibold text-white/80 hover:border-white/30"
            href="/docs/orientation/vocabulary"
          >
            Read the product concepts
          </Link>
        </div>
      </div>
    </section>
  );
}
