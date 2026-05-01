import Link from "next/link";

/**
 * Hero figure — the "wow" piece. A multi-panel dashboard mock that
 * fills the full hero width and shows three Ship surfaces side by side:
 *
 *   ┌─────────────────────────────┬───────────────┐
 *   │  PROCESS · Development      │  INBOX (3)    │
 *   │  Intake → Analysis → Build  │  · clari ELS… │
 *   │     → Review → Done         │  · approv #43 │
 *   │  ticket cards inside        │  · improv     │
 *   ├─────────────────────────────┴───────────────┤
 *   │  ROUTINES TODAY  06:00 ✓ ▸ 08:00 ✓ ▸ …      │
 *   └────────────────────────────────────────────-┘
 *
 * Real ELS-* / KB-* ticket ids come straight out of the repo's git
 * history. Pulse animation on the LIVE chip uses Tailwind's
 * `animate-pulse`. Specialist names match the shipped catalogue.
 */

type FlowState = {
  id: string;
  name: string;
  specialist: string;
  tickets: string[];
  active: number;
  tone: "active" | "queue" | "idle";
};

const HERO_FLOW: FlowState[] = [
  { id: "intake", name: "Intake", specialist: "Intake", tickets: ["KB-7", "ELS-58", "ELS-57"], active: 3, tone: "queue" },
  { id: "analysis", name: "Analysis", specialist: "Business analyst", tickets: ["ELS-55", "ELS-54"], active: 2, tone: "active" },
  { id: "build", name: "Build", specialist: "Developer", tickets: ["KB-3", "KB-5", "ELS-39", "ELS-52"], active: 4, tone: "active" },
  { id: "review", name: "Review", specialist: "Code reviewer", tickets: ["ELS-37"], active: 1, tone: "queue" },
  { id: "done", name: "Done", specialist: "—", tickets: ["+12 this week"], active: 12, tone: "idle" },
];

type InboxItem = {
  kind: "clarification" | "approval" | "improvement";
  ticket: string;
  body: string;
};

const HERO_INBOX: InboxItem[] = [
  { kind: "clarification", ticket: "ELS-54", body: "Should the navigator emit retrieval citations inline?" },
  { kind: "approval", ticket: "PR #43", body: "Knowledge synth — bucket-promotion path. Ready to merge." },
  { kind: "improvement", ticket: "ELS-37", body: "Distiller cron could move from 06:00 to 04:00 — fewer ticket hops." },
];

type Routine = { time: string; name: string; status: "done" | "next" | "later" };

const HERO_ROUTINES: Routine[] = [
  { time: "06:00", name: "Security review", status: "done" },
  { time: "08:00", name: "Daily digest", status: "done" },
  { time: "09:00", name: "Standup", status: "done" },
  { time: "10:00", name: "Architecture review", status: "next" },
  { time: "16:00", name: "Retro", status: "later" },
];

const TONE_DOT: Record<FlowState["tone"], string> = {
  active: "bg-aqua",
  queue: "bg-sun/80",
  idle: "bg-white/30",
};

const TONE_GLOW: Record<FlowState["tone"], string> = {
  active: "shadow-[0_0_24px_-6px_rgba(46,230,214,0.55)] ring-1 ring-aqua/30",
  queue: "ring-1 ring-sun/15",
  idle: "ring-1 ring-white/5",
};

const KIND_LABEL: Record<InboxItem["kind"], string> = {
  clarification: "Clarification",
  approval: "Approval",
  improvement: "Improvement",
};

const KIND_ACCENT: Record<InboxItem["kind"], string> = {
  clarification: "text-sun border-sun/40 bg-sun/10",
  approval: "text-aqua border-aqua/40 bg-aqua/10",
  improvement: "text-lilac border-lilac/40 bg-lilac/10",
};

const STATUS_GLYPH: Record<Routine["status"], { glyph: string; color: string }> = {
  done: { glyph: "✓", color: "text-aqua" },
  next: { glyph: "▸", color: "text-sun" },
  later: { glyph: "·", color: "text-white/30" },
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
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_70%_45%_at_50%_-10%,rgba(46,230,214,0.10),transparent_60%)]" />
      <div className="relative mx-auto max-w-[88rem] px-4 sm:px-6">
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

        <HeroDashboard />

        <div className="mt-10 flex flex-col flex-wrap gap-4 sm:flex-row sm:items-center">
          <Link className="btn-primary text-center sm:text-left" href="/beta">
            Request closed-beta access
          </Link>
          <Link className="btn-ghost text-center" href="/process">
            See how the process works
          </Link>
        </div>

        <nav
          aria-label="Key product routes"
          className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm font-semibold text-white/55"
        >
          <Link className="text-sun transition hover:text-white" href="/docs">
            Docs
          </Link>
          <span className="text-white/25" aria-hidden>·</span>
          <Link className="text-sun transition hover:text-white" href="/use-cases">
            Use cases
          </Link>
          <span className="text-white/25" aria-hidden>·</span>
          <Link className="text-sun transition hover:text-white" href="/process">
            Process &amp; specialists
          </Link>
          <span className="text-white/25" aria-hidden>·</span>
          <Link className="text-sun transition hover:text-white" href="/roadmap">
            Roadmap
          </Link>
        </nav>
      </div>
    </section>
  );
}

function HeroDashboard() {
  return (
    <figure className="mt-12 sm:mt-14" aria-label="Ship workspace dashboard preview">
      <div className="relative rounded-3xl border border-white/10 bg-gradient-to-br from-aqua/[0.07] via-white/[0.02] to-coral/[0.06] p-px shadow-[0_50px_120px_-30px_rgba(46,230,214,0.30),0_30px_80px_-40px_rgba(255,138,128,0.18)]">
        {/* Outer glow accent */}
        <div className="pointer-events-none absolute -inset-4 -z-10 bg-[radial-gradient(ellipse_60%_50%_at_50%_50%,rgba(46,230,214,0.10),transparent_70%)]" />

        <div className="overflow-hidden rounded-[calc(1.5rem-1px)] bg-[#05060d] ring-1 ring-black/40">
          {/* Top toolbar */}
          <div className="flex items-center justify-between gap-3 border-b border-white/10 bg-gradient-to-b from-white/[0.04] to-transparent px-5 py-3">
            <div className="flex items-center gap-2.5">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-coral/55" aria-hidden />
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-sun/55" aria-hidden />
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-aqua/55" aria-hidden />
              <span className="ml-3 font-mono text-[11px] text-white/40">workspace ·</span>
              <span className="font-display text-sm font-bold text-white">elship</span>
              <span className="font-mono text-[11px] text-white/30">/</span>
              <span className="font-display text-sm font-bold text-white">development</span>
              <span className="ml-2 inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-aqua">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inset-0 animate-ping rounded-full bg-aqua/60" />
                  <span className="relative h-1.5 w-1.5 rounded-full bg-aqua" />
                </span>
                Live
              </span>
            </div>
            <div className="hidden items-center gap-4 font-mono text-[11px] text-white/45 sm:flex">
              <span><span className="text-aqua/80">15</span> specialists</span>
              <span className="text-white/15">·</span>
              <span><span className="text-aqua/80">8</span> routines</span>
              <span className="text-white/15">·</span>
              <span><span className="text-aqua/80">22</span> tickets in flight</span>
            </div>
          </div>

          {/* Two-pane main: process flow (left, large) + inbox (right) */}
          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_22rem]">
            {/* Process pane */}
            <div className="border-b border-white/10 lg:border-b-0 lg:border-r">
              <div className="flex items-center justify-between border-b border-white/[0.06] bg-white/[0.015] px-5 py-2.5">
                <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-aqua/85">Process · 8 states</p>
                <p className="font-mono text-[10px] text-white/35">requirements · implementation · qa</p>
              </div>
              <div className="px-4 py-5 sm:px-5">
                <div className="flex items-stretch gap-1.5 overflow-x-auto pb-2 sm:gap-2">
                  {HERO_FLOW.map((state, i) => (
                    <div key={state.id} className="flex flex-1 items-stretch">
                      <div
                        className={`relative flex w-full min-w-[140px] flex-col rounded-xl border border-white/[0.08] bg-gradient-to-b from-white/[0.04] to-transparent px-3.5 py-3 transition sm:min-w-0 ${TONE_GLOW[state.tone]}`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/55">
                            {state.name}
                          </p>
                          <span className={`inline-block h-1.5 w-1.5 rounded-full ${TONE_DOT[state.tone]}`} aria-hidden />
                        </div>
                        <p className="mt-2.5 truncate text-[11px] font-semibold text-white/85">
                          {state.specialist}
                        </p>
                        <ul className="mt-3 flex-1 space-y-1">
                          {state.tickets.slice(0, 3).map((ticket) => (
                            <li
                              key={ticket}
                              className="rounded-md border border-white/[0.06] bg-black/30 px-1.5 py-1 font-mono text-[10px] text-white/65"
                            >
                              {ticket}
                            </li>
                          ))}
                        </ul>
                        <p className="mt-3 font-mono text-[9px] uppercase tracking-[0.14em] text-white/40">
                          {state.active} active
                        </p>
                      </div>
                      {i < HERO_FLOW.length - 1 && (
                        <div className="flex w-3 shrink-0 items-center justify-center text-aqua/30 sm:w-4" aria-hidden>
                          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                            <path d="M2 7h9M8 3l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Inbox pane */}
            <div className="bg-gradient-to-b from-coral/[0.03] to-transparent">
              <div className="flex items-center justify-between border-b border-white/[0.06] bg-white/[0.015] px-5 py-2.5">
                <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-coral/85">Inbox · 3 to decide</p>
                <p className="font-mono text-[10px] text-white/35">routed by handle</p>
              </div>
              <ul className="divide-y divide-white/[0.05]">
                {HERO_INBOX.map((item) => (
                  <li key={item.ticket} className="px-5 py-4">
                    <div className="flex items-start gap-2.5">
                      <span
                        className={`mt-0.5 inline-flex shrink-0 items-center rounded-md border px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-[0.14em] ${KIND_ACCENT[item.kind]}`}
                      >
                        {KIND_LABEL[item.kind]}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono text-[10px] text-white/55">{item.ticket}</p>
                        <p className="mt-1 text-[12px] leading-snug text-white/85">{item.body}</p>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {/* Routines status strip */}
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-white/[0.06] bg-gradient-to-r from-white/[0.025] via-aqua/[0.04] to-white/[0.025] px-5 py-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">Routines today</p>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 font-mono text-[11px]">
              {HERO_ROUTINES.map((routine) => (
                <span key={routine.name} className="flex items-center gap-1.5">
                  <span className={`font-bold ${STATUS_GLYPH[routine.status].color}`}>
                    {STATUS_GLYPH[routine.status].glyph}
                  </span>
                  <span className="text-aqua/65">{routine.time}</span>
                  <span className={routine.status === "later" ? "text-white/40" : "text-white/75"}>
                    {routine.name}
                  </span>
                </span>
              ))}
            </div>
            <span className="ml-auto inline-flex items-center gap-1.5 font-mono text-[10px] text-white/45">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inset-0 animate-ping rounded-full bg-sun/60" />
                <span className="relative h-1.5 w-1.5 rounded-full bg-sun" />
              </span>
              <span>Architecture review starting · 10:00 UTC</span>
            </span>
          </div>
        </div>
      </div>
      <figcaption className="mt-4 text-center text-xs text-white/45">
        The Development process — 8 states, 15 specialists, 8 scheduled routines. Live workspace view, not a marketing diagram.
      </figcaption>
    </figure>
  );
}
