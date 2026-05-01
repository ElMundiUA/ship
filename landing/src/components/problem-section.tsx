/**
 * "The problem" section — second beat of the homepage narrative.
 *
 * Pairs an honest pain (invisible processes drift) with a one-line
 * resolution (legible processes can be improved). Two columns with a
 * stylised SVG infographic per side: chaotic on the left, structured
 * on the right. The infographic is hand-drawn SVG (no Tailwind dynamic
 * classes inside) to keep the contrast visceral.
 */
export function ProblemSection() {
  return (
    <section className="border-y border-white/10 bg-gradient-to-b from-black/20 via-black/40 to-black/20 py-16 sm:py-24">
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <div className="mx-auto max-w-3xl text-center">
          <p className="text-xs font-bold uppercase tracking-[0.2em] text-white/55">The problem</p>
          <h2 className="font-display mt-3 text-3xl font-bold text-white sm:text-4xl lg:text-[2.75rem]">
            Most product companies run on processes nobody can read.
          </h2>
          <p className="mt-5 text-base leading-relaxed text-white/65 sm:text-lg">
            And then they hand AI agents the keys.
          </p>
        </div>

        <div className="mt-14 grid items-stretch gap-6 lg:grid-cols-2 lg:gap-8">
          <div className="flex h-full flex-col rounded-3xl border border-white/10 bg-white/[0.02] p-6 sm:p-8">
            <p className="text-[11px] font-bold uppercase tracking-[0.2em] text-coral/85">Before Ship</p>
            <h3 className="font-display mt-3 text-2xl font-bold text-white sm:text-3xl">
              Invisible processes drift.
            </h3>
            <ChaosInfographic />
            <p className="mt-6 text-sm leading-relaxed text-white/65 sm:text-base">
              The process exists — just not in writing. It lives in the muscle memory of three senior engineers, in a
              Slack thread from last quarter, in a wiki page nobody updates. When two people disagree, neither version
              wins. When agents start running, they amplify the disagreement at machine speed.
            </p>
          </div>

          <div className="flex h-full flex-col rounded-3xl border border-aqua/25 bg-gradient-to-br from-aqua/[0.06] via-white/[0.02] to-transparent p-6 sm:p-8">
            <p className="text-[11px] font-bold uppercase tracking-[0.2em] text-aqua/85">With Ship</p>
            <h3 className="font-display mt-3 text-2xl font-bold text-white sm:text-3xl">
              Written-down processes can be improved.
            </h3>
            <OrderInfographic />
            <p className="mt-6 text-sm leading-relaxed text-white/65 sm:text-base">
              A process you can read is a process you can argue about — and then change. Ship gives every workspace a
              place to write the process down: states, transitions, owners, the routines that fire along the way. Once
              the process is legible, the work that follows it is trackable, and the work that breaks it is visible.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}

function ChaosInfographic() {
  return (
    <div className="mt-6 flex h-44 items-center justify-center rounded-2xl border border-white/10 bg-black/30 p-4">
      <svg viewBox="0 0 320 140" className="h-full w-full" role="img" aria-label="Tangled, undirected paths between scattered ticket dots">
        <defs>
          <radialGradient id="chaos-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgba(255,138,128,0.18)" />
            <stop offset="100%" stopColor="rgba(255,138,128,0)" />
          </radialGradient>
        </defs>
        <rect width="320" height="140" fill="url(#chaos-glow)" />
        {/* Tangled paths */}
        <g stroke="rgba(255,138,128,0.5)" strokeWidth="1.5" fill="none" strokeLinecap="round">
          <path d="M30 30 C 80 90, 130 20, 180 80 S 240 30, 290 100" />
          <path d="M40 100 C 90 40, 150 110, 200 40 S 260 110, 300 50" />
          <path d="M60 60 C 100 100, 160 60, 220 100 S 280 60, 310 90" strokeOpacity="0.35" />
          <path d="M20 80 C 70 30, 130 100, 180 30 S 250 100, 300 30" strokeOpacity="0.25" />
        </g>
        {/* Scattered ticket dots */}
        <g fill="rgba(255,138,128,0.85)">
          <circle cx="30" cy="30" r="4" />
          <circle cx="90" cy="80" r="3" />
          <circle cx="150" cy="40" r="4" />
          <circle cx="210" cy="95" r="3" />
          <circle cx="270" cy="50" r="4" />
          <circle cx="60" cy="115" r="3" />
          <circle cx="180" cy="20" r="3" />
          <circle cx="240" cy="115" r="4" />
          <circle cx="300" cy="80" r="3" />
        </g>
        {/* Stress glyph */}
        <text x="155" y="78" textAnchor="middle" fontSize="22" fill="rgba(255,138,128,0.7)" fontWeight="700">?</text>
      </svg>
    </div>
  );
}

function OrderInfographic() {
  return (
    <div className="mt-6 flex h-44 items-center justify-center rounded-2xl border border-aqua/20 bg-black/30 p-4">
      <svg viewBox="0 0 320 140" className="h-full w-full" role="img" aria-label="Ordered horizontal flow of states with directional arrows">
        <defs>
          <linearGradient id="order-row" x1="0" x2="1">
            <stop offset="0%" stopColor="rgba(46,230,214,0.06)" />
            <stop offset="100%" stopColor="rgba(46,230,214,0)" />
          </linearGradient>
        </defs>
        <rect width="320" height="140" fill="url(#order-row)" />
        {/* Connecting line */}
        <line x1="40" y1="70" x2="280" y2="70" stroke="rgba(46,230,214,0.4)" strokeWidth="1.5" strokeDasharray="3 4" />
        {/* State nodes */}
        {[
          { cx: 40, label: "Intake" },
          { cx: 100, label: "Plan" },
          { cx: 160, label: "Build" },
          { cx: 220, label: "Review" },
          { cx: 280, label: "Done" },
        ].map((node, idx, arr) => (
          <g key={node.label}>
            <circle cx={node.cx} cy={70} r="9" fill="rgba(46,230,214,0.18)" stroke="rgba(46,230,214,0.85)" strokeWidth="1.5" />
            {idx === arr.length - 1 ? (
              <circle cx={node.cx} cy={70} r="4" fill="rgba(46,230,214,0.85)" />
            ) : null}
            <text x={node.cx} y={48} textAnchor="middle" fontSize="9" fill="rgba(255,255,255,0.7)" fontWeight="600" letterSpacing="0.05em">
              {node.label.toUpperCase()}
            </text>
          </g>
        ))}
        {/* Arrowheads */}
        {[60, 120, 180, 240].map((x) => (
          <path key={x} d={`M ${x} 67 L ${x + 8} 70 L ${x} 73 Z`} fill="rgba(46,230,214,0.55)" />
        ))}
        {/* Routine ticks */}
        <g fill="rgba(255,200,87,0.7)">
          <rect x="98" y="100" width="4" height="8" rx="1" />
          <rect x="158" y="100" width="4" height="8" rx="1" />
          <rect x="218" y="100" width="4" height="8" rx="1" />
        </g>
        <text x="160" y="125" textAnchor="middle" fontSize="8" fill="rgba(255,200,87,0.6)" letterSpacing="0.08em">
          ROUTINES FIRING
        </text>
      </svg>
    </div>
  );
}
