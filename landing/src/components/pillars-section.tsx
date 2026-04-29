const pillars = [
  {
    title: "One story for the room",
    body: "Product owners see outcomes and decisions; engineers see filenames and gates. Ship keeps both tied to the same evidence trail.",
    accent: "from-coral/30 to-transparent",
  },
  {
    title: "Adoption that fits you",
    body: "Connect the repo, tracker, knowledge, and team policies you already use. No forced migration to one vendor stack.",
    accent: "from-aqua/30 to-transparent",
  },
  {
    title: "Evidence you can show",
    body: "Tickets, pull requests, checks, and knowledge updates stay linked so procurement, security, and team leads can review the trail.",
    accent: "from-lilac/35 to-transparent",
  },
];

export function PillarsSection() {
  return (
    <section id="method" className="border-y border-white/10 bg-black/20 py-20 sm:py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <h2 className="font-display text-3xl font-bold text-white sm:text-4xl">Why teams buy control, not another AI demo</h2>
        <p className="mt-4 max-w-2xl text-white/65">
          Ship keeps autonomy bounded: humans own intent, automation leaves evidence, and vendors stay replaceable.
          The console makes that discipline visible without hiding the contracts engineers need to review.
        </p>
        <div className="mt-12 grid gap-6 md:grid-cols-3">
          {pillars.map((p) => (
            <article
              key={p.title}
              className="group relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-white/[0.07] to-transparent p-8 shadow-card"
            >
              <div
                className={`pointer-events-none absolute -right-8 -top-8 h-40 w-40 rounded-full bg-gradient-to-br ${p.accent} blur-2xl`}
              />
              <h3 className="font-display relative text-xl font-bold text-white">{p.title}</h3>
              <p className="relative mt-4 text-sm leading-relaxed text-white/70">{p.body}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
