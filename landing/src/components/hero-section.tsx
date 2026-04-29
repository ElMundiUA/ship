import Link from "next/link";
import { AdoptionWizardButton } from "@/components/adoption-wizard";
import pkg from "../../package.json";

const HERO_CHECKLIST = [
  "Connect the repo and tracker",
  "See blockers and shipped work",
  "Keep decisions tied to evidence",
];

const KIT_VERSION = `v${pkg.version}`;

export function HeroSection() {
  return (
    <section className="relative overflow-hidden pt-28 pb-20 sm:pt-32 sm:pb-28">
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.06'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E\")",
        }}
      />
      <div className="relative mx-auto max-w-6xl px-4 sm:px-6">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.06] px-4 py-1.5 text-xs font-semibold uppercase tracking-widest text-aqua">
          <span>Product delivery workspace · {KIT_VERSION}</span>
          <span aria-hidden className="text-white/30">·</span>
          <span className="text-white/75">console + evidence trail</span>
        </p>
        <h1 className="font-display max-w-5xl text-[2.125rem] font-bold leading-[1.08] tracking-normal text-white sm:text-5xl sm:leading-[1.06] md:text-6xl md:leading-[1.05] lg:text-[3.45rem] lg:leading-[1.03]">
          Give product owners a{" "}
          <span className="bg-gradient-to-r from-coral via-sun to-aqua bg-clip-text text-transparent">
            clear cockpit
          </span>
          {" "}for AI-assisted delivery.
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-white/70 sm:text-xl md:text-[1.35rem] md:leading-relaxed">
          Ship connects your repo, tracker, automation, and knowledge into one workspace. Product owners see what is moving,
          what is blocked, who decided, and which evidence backs the work; engineers still keep prompts, rules, and setup
          versioned where they can be reviewed.
        </p>

        <figure className="mt-10 max-w-4xl" aria-label="Ship workspace checklist">
          <div className="relative rounded-2xl border border-aqua/30 bg-gradient-to-br from-aqua/[0.10] via-white/[0.02] to-coral/10 p-px shadow-[0_28px_90px_-40px_rgba(46,230,214,0.45)]">
            <div className="overflow-hidden rounded-[calc(1rem-1px)] bg-[#05060d] ring-1 ring-black/50">
              <div className="flex items-center justify-between border-b border-white/10 px-4 py-2 text-[11px] font-semibold uppercase tracking-widest text-white/50">
                <span className="flex items-center gap-2">
                  <span className="inline-block h-2 w-2 rounded-full bg-coral/80" aria-hidden />
                  <span className="inline-block h-2 w-2 rounded-full bg-sun/80" aria-hidden />
                  <span className="inline-block h-2 w-2 rounded-full bg-aqua/80" aria-hidden />
                  <span className="ml-2">Workspace setup</span>
                </span>
                <span className="hidden sm:inline text-white/40">Product owner view</span>
              </div>
              <div className="grid gap-3 px-4 py-5 sm:grid-cols-3">
                {HERO_CHECKLIST.map((item) => (
                  <div key={item} className="rounded-xl border border-white/10 bg-white/[0.04] p-4">
                    <div className="mb-3 h-2 w-2 rounded-full bg-aqua" aria-hidden />
                    <p className="text-sm font-semibold text-white">{item}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <figcaption className="mt-3 text-xs text-white/45">
            The first screen is for decisions and evidence. Terminal setup is still available for teams that want local,
            reviewable control.
          </figcaption>
        </figure>

        <div className="mt-10 flex flex-col flex-wrap gap-4 sm:flex-row sm:items-center">
          <Link className="btn-primary text-center sm:text-left" href="/getting-started">
            Plan your workspace
          </Link>
          <Link className="btn-secondary text-center" href="/use-cases">
            See product use cases
          </Link>
          <AdoptionWizardButton className="btn-secondary text-center">
            Create adoption brief
          </AdoptionWizardButton>
        </div>

        <nav
          aria-label="Key kit routes"
          className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm font-semibold text-white/55"
        >
          <Link className="text-aqua transition hover:text-white" href="/docs">
            Docs
          </Link>
          <span className="text-white/25" aria-hidden>·</span>
          <Link className="text-aqua transition hover:text-white" href="/use-cases">
            Use cases
          </Link>
          <span className="text-white/25" aria-hidden>·</span>
          <Link className="text-aqua transition hover:text-white" href="/tools">
            Tools
          </Link>
          <span className="text-white/25" aria-hidden>·</span>
          <Link className="text-aqua transition hover:text-white" href="/collections">
            Collections
          </Link>
          <span className="text-white/25" aria-hidden>·</span>
          <Link className="text-aqua transition hover:text-white" href="/patterns">
            Patterns
          </Link>
        </nav>

        <div className="mt-14 grid gap-4 sm:grid-cols-3">
          {[
            {
              k: "One workspace",
              v: "Connect the product repo, tracker, knowledge, and team settings before work starts moving.",
              code: "Workspace + repo",
            },
            {
              k: "One attention surface",
              v: "Clarifications, improvements, failures, and approvals land where an owner can make a decision.",
              code: "Inbox",
            },
            {
              k: "One audit trail",
              v: "Tickets, pull requests, checks, and knowledge updates stay linked so the story survives review.",
              code: "Evidence",
            },
          ].map((item) => (
            <div key={item.k} className="glass-panel p-5">
              <p className="font-display text-sm font-bold text-aqua">{item.k}</p>
              <p className="mt-2 text-sm leading-relaxed text-white/65">{item.v}</p>
              <code className="mt-3 block truncate rounded-md bg-black/40 px-2 py-1 font-mono text-[11px] text-white/65">
                {item.code}
              </code>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
