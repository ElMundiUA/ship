import Link from "next/link";

const tiles = [
  {
    href: "/tools",
    kicker: "Integrations",
    title: "Tools",
    body: "Who plugs into what — trackers, CI, browsers, agents — spelled out so security and platform teams can review once.",
  },
  {
    href: "/patterns",
    kicker: "Source of Plays",
    title: "Patterns",
    body: "Versioned Markdown procedures — PR self-review, release cuts, knowledge refreshes — that the operator console renders as Plays you assign as Automations.",
  },
  {
    href: "/collections",
    kicker: "Starter bundles",
    title: "Collections",
    body: "Curated stacks for common product shapes so a new team does not start from a blank wiki page.",
  },
  {
    href: "/use-cases",
    kicker: "Field proof",
    title: "Use cases",
    body: "Reference org and product story with screenshots — the fastest way to answer “has anyone actually run this?”",
  },
];

export function KitSurfaceSection() {
  return (
    <section id="kit" className="border-y border-white/10 bg-black/30 py-20 sm:py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">What you get in the box</p>
        <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">Docs, book, and catalogs — one experience</h2>
        <p className="mt-4 max-w-3xl text-lg text-white/65">
          Buyers read the narrative; operators browse tools and patterns; engineers deep-link into chapters. Everything
          ships from the same repository so the story and the wiring cannot silently diverge.
        </p>
        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {tiles.map((t) => (
            <Link
              key={t.href}
              href={t.href}
              className="group flex flex-col rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.06] to-transparent p-6 shadow-card transition hover:border-aqua/35 hover:shadow-glow"
            >
              <p className="text-[10px] font-bold uppercase tracking-widest text-white/40">{t.kicker}</p>
              <h3 className="font-display mt-2 text-lg font-bold text-white group-hover:text-aqua">{t.title}</h3>
              <p className="mt-3 flex-1 text-sm leading-relaxed text-white/60">{t.body}</p>
              <span className="mt-4 text-xs font-semibold text-aqua">Explore →</span>
            </Link>
          ))}
        </div>

        <div className="mt-10 grid gap-5 md:grid-cols-2">
          <div className="rounded-2xl border border-aqua/25 bg-gradient-to-br from-aqua/[0.08] to-transparent p-6 sm:p-8">
            <p className="text-xs font-bold uppercase tracking-widest text-aqua/90">Toolchain</p>
            <h3 className="font-display mt-2 text-xl font-bold text-white">Ship CLI — unified control</h3>
            <p className="mt-3 text-sm leading-relaxed text-white/65">
              One terminal entry point lists patterns, tools, and collections from the same manifests this site
              renders — so operators, scripts, and agents never argue about which copy is “current.”
            </p>
            <p className="mt-4 text-xs text-white/45">Shipped with the repo; documented under Getting started.</p>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 sm:p-8">
            <p className="text-xs font-bold uppercase tracking-widest text-white/40">Operator console</p>
            <h3 className="font-display mt-2 text-xl font-bold text-white">Inbox · Plays · Automations · Runs</h3>
            <p className="mt-3 text-sm leading-relaxed text-white/65">
              Four product nouns mirror the four protocol surfaces. The Navigator agent — same chat window — has tools
              for every one: triage the Inbox, run a Play, toggle an Automation, query Runs. Members can ask, admins
              can mutate.
            </p>
            <p className="mt-4 text-xs text-white/45">
              Shipped today; documented under{" "}
              <Link className="text-aqua hover:text-white" href="/docs/concepts">
                Concepts
              </Link>
              .
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
