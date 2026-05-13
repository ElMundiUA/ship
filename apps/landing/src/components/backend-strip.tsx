import Link from "next/link";

const capabilities = [
  {
    title: "Search workspace knowledge",
    body: "Ask in plain language and get pointed to the right docs, policies, and product context before an agent acts.",
  },
  {
    title: "Pull the exact policy",
    body: "Retrieve the complete rule or procedure so summaries stay faithful to the source and automation does not improvise.",
  },
  {
    title: "File structured improvement notes",
    body: "Turn retro-style input into a tracked Inbox item your engineering org already knows how to triage — with guardrails so sensitive fragments do not leak.",
  },
];

export function BackendStrip() {
  return (
    <section id="api" className="border-y border-white/10 bg-gradient-to-br from-aqua/10 via-transparent to-lilac/10 py-20 sm:py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <div className="grid gap-10 lg:grid-cols-2 lg:items-center">
          <div>
            <p className="text-sm font-bold uppercase tracking-widest text-aqua">For agent wiring</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">A small API behind the owner workspace</h2>
            <p className="mt-4 text-white/65">
              The product workspace stands on its own. When you wire agents, the companion service lets them query the
              same docs, policies, and knowledge humans review without scraping HTML.
            </p>
            <p className="mt-4 text-sm text-white/50">
              Product owners stay in the console; automation reads bounded context and reports evidence back into the
              same record.
            </p>
            <Link href="/docs/process/overview" className="btn-secondary mt-8 inline-flex">
              Technical reference
            </Link>
          </div>
          <div className="glass-panel divide-y divide-white/10 overflow-hidden">
            {capabilities.map((c) => (
              <div key={c.title} className="px-5 py-5 sm:px-6">
                <p className="font-display text-base font-semibold text-white">{c.title}</p>
                <p className="mt-2 text-sm leading-relaxed text-white/60">{c.body}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
