import Link from "next/link";

const tiles = [
  {
    href: "/beta",
    kicker: "Control",
    title: "Workspace",
    body: "The owner, repos, tracker, members, and integrations that make the product delivery loop visible.",
  },
  {
    href: "/process",
    kicker: "Boundaries",
    title: "Policies",
    body: "Short standing rules for what agents may do, when a human must decide, and which evidence is required.",
  },
  {
    href: "/docs/knowledge/overview",
    kicker: "Context",
    title: "Knowledge",
    body: "Product facts, repo context, constraints, and review notes agents can use without guessing.",
  },
  {
    href: "/docs/operating/morning-loop",
    kicker: "Trust",
    title: "Evidence",
    body: "Tickets, pull requests, checks, comments, and Inbox decisions tied together for review.",
  },
];

export function KitSurfaceSection() {
  return (
    <section id="kit" className="border-y border-white/10 bg-black/30 py-20 sm:py-24">
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">What you get</p>
        <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">Workspace, policies, knowledge, and evidence</h2>
        <p className="mt-4 max-w-3xl text-lg text-white/65">
          Solo founders and product owners get the product view first. Engineering can still inspect the underlying
          contracts, but the public surface is about ownership, boundaries, and proof.
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
            <p className="text-xs font-bold uppercase tracking-widest text-aqua/90">Policies</p>
            <h3 className="font-display mt-2 text-xl font-bold text-white">Rules before motion</h3>
            <p className="mt-3 text-sm leading-relaxed text-white/65">
              Policies are the short, explicit boundaries for agent-assisted work: allowed repos, review gates, required
              evidence, and the moments where a human must decide.
            </p>
            <p className="mt-4 text-xs text-white/45">They turn “be careful” into reviewable rules.</p>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 sm:p-8">
            <p className="text-xs font-bold uppercase tracking-widest text-white/40">Owner console</p>
            <h3 className="font-display mt-2 text-xl font-bold text-white">Dashboard · Inbox · Knowledge · Integrations</h3>
            <p className="mt-3 text-sm leading-relaxed text-white/65">
              The console shows the live workspace: health, work in progress, shipped outcomes, repo wiring, decisions,
              and knowledge that agents can use without improvising.
            </p>
            <p className="mt-4 text-xs text-white/45">
              Shipped today; documented under{" "}
              <Link className="text-aqua hover:text-white" href="/docs/orientation/vocabulary">
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
