import Link from "next/link";

/**
 * "The model" section — third beat of the homepage narrative.
 *
 * Condensed pitch of the four-layer stack (workspace → process →
 * routine → specialist → executor) with a labelled column infographic
 * on the right and a short prose pitch on the left. Cross-links to
 * /process for the deep walkthrough so this section stays a teaser,
 * not a re-explanation.
 */
export function ModelSection() {
  return (
    <section className="border-b border-white/10 py-16 sm:py-24">
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <div className="grid items-stretch gap-10 lg:grid-cols-12 lg:gap-14">
          <div className="flex h-full flex-col lg:col-span-7">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">The model</p>
            <h2 className="font-display mt-3 text-3xl font-bold text-white sm:text-4xl lg:text-[2.75rem] lg:leading-[1.06]">
              Process on specialists. Specialists on executors.
            </h2>
            <p className="mt-5 text-base leading-relaxed text-white/70 sm:text-lg">
              Ship is built around a small, layered model. Every workspace holds a graph of <span className="text-white">processes</span>.
              Every process is a sequence of <span className="text-white">states</span>; <span className="text-white">routines</span> fire
              on schedule along the way. Each routine takes on a <span className="text-white">specialist</span> — a versioned role —
              and runs through an <span className="text-white">executor</span> — your AI agent of choice.
            </p>
            <p className="mt-4 text-base leading-relaxed text-white/65 sm:text-lg">
              Swap Cursor for Codex tomorrow and the routine, specialist, and process don&apos;t change — only the
              executor does. That separation is the whole product.
            </p>
            <div className="mt-auto pt-8">
              <div className="flex flex-wrap gap-3">
                <Link href="/process" className="btn-primary inline-flex">
                  See the full model
                </Link>
                <Link
                  href="/docs/orientation/vocabulary"
                  className="btn-ghost inline-flex"
                >
                  Read the vocabulary
                </Link>
              </div>
            </div>
          </div>

          <div className="lg:col-span-5">
            <ModelStack />
          </div>
        </div>
      </div>
    </section>
  );
}

function ModelStack() {
  const layers = [
    {
      kicker: "WORKSPACE",
      title: "A graph of processes",
      example: "Development · Marketing · Support · …",
      accent: "aqua" as const,
    },
    {
      kicker: "PROCESS",
      title: "States and transitions",
      example: "Intake → Analysis → Build → Review → Done",
      accent: "aqua" as const,
    },
    {
      kicker: "ROUTINE",
      title: "Scheduled job inside a process",
      example: "daily_security_review · 06:00 daily",
      accent: "sun" as const,
    },
    {
      kicker: "SPECIALIST",
      title: "Versioned role definition",
      example: "developer · architect · QA · 12 more",
      accent: "lilac" as const,
    },
    {
      kicker: "EXECUTOR",
      title: "The agent that runs the prompt",
      example: "Cursor · Claude · Codex · Copilot · …",
      accent: "coral" as const,
    },
  ];

  const dotClass: Record<(typeof layers)[number]["accent"], string> = {
    aqua: "bg-aqua",
    sun: "bg-sun",
    lilac: "bg-lilac",
    coral: "bg-coral",
  };
  const kickerClass: Record<(typeof layers)[number]["accent"], string> = {
    aqua: "text-aqua/85",
    sun: "text-sun/85",
    lilac: "text-lilac/85",
    coral: "text-coral/85",
  };

  return (
    <div className="relative h-full rounded-2xl border border-white/10 bg-gradient-to-b from-black/30 via-black/40 to-black/30 p-3 sm:p-4">
      <div className="flex flex-col gap-2.5">
        {layers.map((layer, idx) => (
          <div key={layer.kicker} className="relative">
            <div className="flex items-start gap-3 rounded-xl border border-white/[0.06] bg-white/[0.025] px-4 py-3.5">
              <span className={`mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${dotClass[layer.accent]}`} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className={`font-mono text-[10px] font-bold uppercase tracking-[0.18em] ${kickerClass[layer.accent]}`}>
                  {layer.kicker}
                </p>
                <p className="font-display mt-1 text-sm font-bold text-white sm:text-base">{layer.title}</p>
                <p className="mt-1 text-[11px] text-white/45 sm:text-xs">{layer.example}</p>
              </div>
            </div>
            {idx < layers.length - 1 && (
              <div className="mx-auto -my-1 flex h-2 w-px items-center justify-center bg-white/15" aria-hidden />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
