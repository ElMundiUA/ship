export function BuildSection() {
  return (
    <section
      id="build"
      aria-labelledby="build-heading"
      className="py-20 sm:py-24"
    >
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <p className="text-sm font-bold uppercase tracking-widest text-coral/90">Step 2</p>
        <h2
          id="build-heading"
          className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl"
        >
          Ship Builds It
        </h2>
        <p className="mt-4 max-w-2xl text-lg leading-relaxed text-white/65">
          Ship turns your description into a real codebase — pages, forms, styles, and tests —
          inside guardrails you can inspect. Specialists scaffold, implement, and review while you
          stay in the loop through a clear audit trail, not a black box.
        </p>
        <div className="mt-10 grid gap-5 md:grid-cols-3">
          {[
            {
              title: "Scaffold & implement",
              body: "Landing pages, auth flows, APIs — built to your spec with tests and lint gates.",
            },
            {
              title: "Review before merge",
              body: "Every change is reviewable. You see what shipped, why, and what to verify.",
            },
            {
              title: "Iterate in plain language",
              body: "Want a different CTA or colour? Describe the tweak — Ship applies it on the branch.",
            },
          ].map((card) => (
            <article
              key={card.title}
              className="rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.05] to-transparent p-6"
            >
              <h3 className="font-display text-lg font-bold text-white">{card.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-white/60">{card.body}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
