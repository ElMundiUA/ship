import Link from "next/link";

const capabilities = [
  {
    title: "Search the methodology library",
    body: "Ask in plain language and get pointed to the right chapters and playbooks — useful when an agent or script needs context before opening a ticket.",
  },
  {
    title: "Pull the full chapter",
    body: "After you pick a path, retrieve the complete text so summaries stay faithful to the source — fewer “telephone game” mistakes in automation.",
  },
  {
    title: "File structured improvement notes",
    body: "Turn retro-style input into a tracked follow-up your engineering org already knows how to triage — with guardrails so sensitive fragments do not leak.",
  },
];

export function BackendStrip() {
  return (
    <section id="api" className="border-y border-white/10 bg-gradient-to-br from-aqua/10 via-transparent to-lilac/10 py-20 sm:py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <div className="grid gap-10 lg:grid-cols-2 lg:items-center">
          <div>
            <p className="text-sm font-bold uppercase tracking-widest text-aqua">For automation teams</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">Optional service when you wire agents at scale</h2>
            <p className="mt-4 text-white/65">
              The marketing site stands on its own. When you are ready, a small companion service lets agents and CI query
              the same documentation humans read — plus list pattern metadata — without scraping HTML.
            </p>
            <p className="mt-4 text-sm text-white/50">
              Catalogs for tools, workflows, and collections stay in-repo; operators usually manage those through the Ship
              CLI without running the service.
            </p>
            <Link href="/docs/tools/backend-api" className="btn-secondary mt-8 inline-flex">
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
