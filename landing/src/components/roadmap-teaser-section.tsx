import Link from "next/link";

/**
 * "Roadmap teaser" section — the sixth beat of the homepage narrative.
 *
 * Three columns (Now / Next / Later) with one line per column, plus
 * the north-star headline that drives the full /roadmap page. Stays
 * tight on purpose — the home is not a roadmap, it points at one.
 */
export function RoadmapTeaserSection() {
  return (
    <section className="border-b border-white/10 py-16 sm:py-24">
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <div className="mx-auto max-w-3xl text-center">
          <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Where this goes</p>
          <h2 className="font-display mt-3 text-3xl font-bold text-white sm:text-4xl lg:text-[2.75rem] lg:leading-[1.06]">
            From sign-up to your first closed ticket: one day for SMB, one week for enterprise.
          </h2>
          <p className="mt-5 text-base leading-relaxed text-white/65 sm:text-lg">
            The north star drives the roadmap. Below — three lines, three time horizons. The full version lives at{" "}
            <Link href="/roadmap" className="text-sun hover:text-white">/roadmap</Link>.
          </p>
        </div>

        <div className="mt-12 grid items-stretch gap-5 md:grid-cols-3">
          <Column
            kicker="Live now"
            accent="aqua"
            title="Production-depth Development process"
            body="Workspace + repo activation, eight-state SDLC, fifteen specialists, eight scheduled routines, structured Inbox, knowledge buckets and distiller."
          />
          <Column
            kicker="Next"
            accent="sun"
            title="Navigator + four new processes"
            body="Best-in-class workspace-aware AI assistant. Marketing, support, documentation, sales processes lifted from draft to production. Slack / Teams / Discord surfaces."
          />
          <Column
            kicker="Later"
            accent="lilac"
            title="Mobile, voice, desktop, SOC 2"
            body="Approve, clarify, decide from the phone (and by voice). A dedicated desktop app. Release process automation. SOC 2 Type II and ISO 27001."
          />
        </div>

        <div className="mt-12 flex flex-wrap justify-center gap-3">
          <Link href="/roadmap" className="btn-primary inline-flex">
            Read the full roadmap
          </Link>
        </div>
      </div>
    </section>
  );
}

const KICKER_CLASS: Record<"aqua" | "sun" | "lilac", string> = {
  aqua: "text-aqua/85",
  sun: "text-sun/85",
  lilac: "text-lilac/85",
};

const DOT_CLASS: Record<"aqua" | "sun" | "lilac", string> = {
  aqua: "bg-aqua",
  sun: "bg-sun",
  lilac: "bg-lilac",
};

function Column({
  kicker,
  accent,
  title,
  body,
}: {
  kicker: string;
  accent: "aqua" | "sun" | "lilac";
  title: string;
  body: string;
}) {
  return (
    <div className="flex h-full flex-col rounded-2xl border border-white/10 bg-white/[0.02] p-6">
      <div className="flex items-center gap-2">
        <span className={`inline-block h-1.5 w-1.5 rounded-full ${DOT_CLASS[accent]}`} aria-hidden />
        <p className={`text-[11px] font-bold uppercase tracking-[0.2em] ${KICKER_CLASS[accent]}`}>{kicker}</p>
      </div>
      <h3 className="font-display mt-3 text-xl font-bold text-white">{title}</h3>
      <p className="mt-3 text-sm leading-relaxed text-white/65">{body}</p>
    </div>
  );
}
