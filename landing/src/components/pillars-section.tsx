const pillars = [
  {
    title: "One story for the room",
    body: "Executives see outcomes and governance; engineers see filenames and gates. Ship keeps both in the same repository so alignment does not depend on a slide deck that aged out last quarter.",
    accent: "from-coral/30 to-transparent",
  },
  {
    title: "Adoption that fits you",
    body: "The wizard captures how you already track work, ship releases, and run agents — then hands off a concrete brief. No forced migration to a single vendor stack.",
    accent: "from-aqua/30 to-transparent",
  },
  {
    title: "Evidence you can show",
    body: "Use cases, screenshots, and published manuals give procurement and security something to click through — not a promise buried in a sales email.",
    accent: "from-lilac/35 to-transparent",
  },
];

export function PillarsSection() {
  return (
    <section id="method" className="border-y border-white/10 bg-black/20 py-20 sm:py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <h2 className="font-display text-3xl font-bold text-white sm:text-4xl">Why teams buy the kit, not a dashboard</h2>
        <p className="mt-4 max-w-2xl text-white/65">
          Apache-2.0, file-backed content, and a public site you can host yourself. You are investing in a shared operating
          model — not renting another pane of glass that goes out of sync the week after rollout.
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
