import { AgentSetupForm } from "@/components/agent-setup-form";

export function CommandBuilderSection() {
  return (
    <section id="command-builder" className="border-y border-white/10 bg-gradient-to-b from-black/40 via-[#05070f] to-black/40 py-20 sm:py-24">
      <div className="mx-auto max-w-5xl px-4 sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-3xl">
            <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">Developer setup</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              Keep local setup reviewable when your team needs it
            </h2>
            <p className="mt-4 text-lg text-white/65">
              Most product owners start in the console. Developers and platform teams can still generate the exact local
              setup command for agent rules, config checks, and reproducible repo wiring. Nothing is uploaded — all of it
              stays in your browser until you copy.
            </p>
          </div>
        </div>

        <div className="mt-10">
          <AgentSetupForm />
        </div>
      </div>
    </section>
  );
}
