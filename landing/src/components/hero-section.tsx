import Link from "next/link";

type FlowState = { name: string; specialist: string; count: number; tone: "active" | "queue" | "idle" };

const HERO_FLOW: FlowState[] = [
  { name: "Intake", specialist: "Intake", count: 3, tone: "queue" },
  { name: "Analysis", specialist: "Business analyst", count: 2, tone: "active" },
  { name: "Build", specialist: "Developer", count: 4, tone: "active" },
  { name: "Review", specialist: "Code reviewer", count: 1, tone: "queue" },
  { name: "Done", specialist: "—", count: 12, tone: "idle" },
];

const HERO_ROUTINES: { time: string; name: string }[] = [
  { time: "06:00", name: "Security review" },
  { time: "08:00", name: "Daily digest" },
  { time: "10:00", name: "Architecture review" },
];

const TONE_DOT: Record<FlowState["tone"], string> = {
  active: "bg-aqua",
  queue: "bg-sun/80",
  idle: "bg-white/30",
};

export function HeroSection() {
  return (
    <section className="relative overflow-hidden pt-28 pb-20 sm:pt-32 sm:pb-28">
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.06'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E\")",
        }}
      />
      <div className="relative mx-auto max-w-6xl px-4 sm:px-6">
        <Link
          href="/beta"
          className="mb-3 inline-flex items-center gap-2 rounded-full border border-aqua/30 bg-aqua/[0.08] px-4 py-1.5 text-xs font-semibold uppercase tracking-widest text-aqua transition hover:border-aqua/55 hover:bg-aqua/[0.14]"
        >
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-aqua" aria-hidden />
          <span>Closed beta</span>
          <span aria-hidden className="text-white/30">·</span>
          <span className="text-aqua/85">Request access →</span>
        </Link>
        <h1 className="font-display max-w-5xl text-[2.125rem] font-bold leading-[1.08] tracking-normal text-white sm:text-5xl sm:leading-[1.06] md:text-6xl md:leading-[1.05] lg:text-[3.45rem] lg:leading-[1.03]">
          Give solo founders and product owners a{" "}
          <span className="bg-gradient-to-r from-coral via-sun to-aqua bg-clip-text text-transparent">
            clear cockpit
          </span>
          {" "}for AI-assisted delivery.
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-white/70 sm:text-xl md:text-[1.35rem] md:leading-relaxed">
          Ship connects your repo, tracker, policies, knowledge, automation, and evidence into one workspace. Owners see
          what is moving, what is blocked, who decided, and which rules bound the work before an agent touches the repo.
        </p>

        <figure className="mt-10 max-w-4xl" aria-label="Development process — Ship workspace preview">
          <div className="relative rounded-2xl border border-white/10 bg-gradient-to-br from-aqua/[0.06] via-white/[0.02] to-transparent p-px shadow-[0_28px_90px_-40px_rgba(46,230,214,0.25)]">
            <div className="overflow-hidden rounded-[calc(1rem-1px)] bg-[#05060d] ring-1 ring-black/40">
              {/* Top bar */}
              <div className="flex items-center justify-between gap-3 border-b border-white/10 bg-white/[0.02] px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="inline-block h-2 w-2 rounded-full bg-white/15" aria-hidden />
                  <span className="inline-block h-2 w-2 rounded-full bg-white/15" aria-hidden />
                  <span className="inline-block h-2 w-2 rounded-full bg-white/15" aria-hidden />
                  <span className="ml-2 font-display text-xs font-bold text-white">Development</span>
                  <span className="hidden rounded-full border border-aqua/40 bg-aqua/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.18em] text-aqua sm:inline">
                    Live
                  </span>
                </div>
                <span className="font-mono text-[10px] text-white/40">8 specialists · 8 routines</span>
              </div>

              {/* States row */}
              <div className="px-4 py-5 sm:px-5">
                <div className="flex items-stretch gap-1.5 overflow-x-auto sm:gap-2">
                  {HERO_FLOW.map((state, i) => (
                    <div key={state.name} className="flex flex-1 items-stretch">
                      <div className="flex w-full min-w-[110px] flex-col rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2.5 sm:min-w-0">
                        <div className="flex items-center justify-between">
                          <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-white/55">
                            {state.name}
                          </p>
                          <span className={`inline-block h-1.5 w-1.5 rounded-full ${TONE_DOT[state.tone]}`} aria-hidden />
                        </div>
                        <p className="mt-2 text-[11px] font-semibold text-white/85">{state.specialist}</p>
                        <p className="mt-2 font-mono text-[9px] text-white/40">{state.count} active</p>
                      </div>
                      {i < HERO_FLOW.length - 1 && (
                        <div className="flex w-3 items-center justify-center text-white/15" aria-hidden>
                          <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                            <path d="M2 5h6M6 2l3 3-3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Routines strip */}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-white/[0.06] bg-white/[0.015] px-4 py-2.5">
                <p className="text-[9px] font-bold uppercase tracking-[0.18em] text-white/40">Today&apos;s routines</p>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] text-white/55">
                  {HERO_ROUTINES.map((routine, i) => (
                    <span key={routine.name} className="flex items-center gap-1.5">
                      <span className="text-aqua/70">{routine.time}</span>
                      <span>{routine.name}</span>
                      {i < HERO_ROUTINES.length - 1 && <span className="text-white/15" aria-hidden>·</span>}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
          <figcaption className="mt-3 text-xs text-white/45">
            The Development process — eight states, fifteen specialists, eight scheduled routines. This is the workspace
            view, not a marketing diagram.
          </figcaption>
        </figure>

        <div className="mt-10 flex flex-col flex-wrap gap-4 sm:flex-row sm:items-center">
          <Link className="btn-primary text-center sm:text-left" href="/beta">
            Request closed-beta access
          </Link>
          <Link className="btn-ghost text-center" href="#operator-loop">
            See how it works
          </Link>
        </div>

        <nav
          aria-label="Key product routes"
          className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm font-semibold text-white/55"
        >
          <Link className="text-aqua transition hover:text-white" href="/docs">
            Docs
          </Link>
          <span className="text-white/25" aria-hidden>·</span>
          <Link className="text-aqua transition hover:text-white" href="/use-cases">
            Use cases
          </Link>
          <span className="text-white/25" aria-hidden>·</span>
          <Link className="text-aqua transition hover:text-white" href="/process">
            Process & specialists
          </Link>
        </nav>

        <div className="mt-14 grid gap-4 sm:grid-cols-3">
          {[
            {
              k: "One workspace",
              v: "Connect the product repo, tracker, knowledge, and policies before work starts moving.",
              code: "Workspace + policies",
            },
            {
              k: "One attention surface",
              v: "Clarifications, improvements, failures, and approvals land where an owner can make a decision.",
              code: "Inbox",
            },
            {
              k: "One audit trail",
              v: "Tickets, pull requests, checks, and knowledge updates stay linked so the story survives review.",
              code: "Evidence",
            },
          ].map((item) => (
            <div key={item.k} className="glass-panel p-5">
              <p className="font-display text-sm font-bold text-aqua">{item.k}</p>
              <p className="mt-2 text-sm leading-relaxed text-white/65">{item.v}</p>
              <code className="mt-3 block truncate rounded-md bg-black/40 px-2 py-1 font-mono text-[11px] text-white/65">
                {item.code}
              </code>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
