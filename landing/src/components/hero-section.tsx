import Link from "next/link";
import { AdoptionWizardButton } from "@/components/adoption-wizard";

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
          Methodology kit · v0.7.0
        </p>
        <h1 className="font-display max-w-5xl text-[2.125rem] font-bold leading-[1.08] tracking-normal text-white sm:text-5xl sm:leading-[1.06] md:text-6xl md:leading-[1.05] lg:text-[3.45rem] lg:leading-[1.03]">
          Ship the{" "}
          <span className="bg-gradient-to-r from-coral via-sun to-aqua bg-clip-text text-transparent">
            methodology
          </span>
          , not another toolchain tax.
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-white/70 sm:text-xl md:text-[1.35rem] md:leading-relaxed">
          Give your organization one place to learn how you deliver: onboarding chapters, long-form narrative for leaders,
          and catalogs that stay aligned with what you actually run. Prove it with published use cases, then let your team
          wire their own tracker and release habits — without buying another black-box platform.
        </p>
        <div className="mt-10 flex flex-col flex-wrap gap-4 sm:flex-row sm:items-center">
          <Link className="btn-primary text-center sm:text-left" href="/docs/getting-started">
            Start here
          </Link>
          <Link className="btn-secondary text-center" href="/book">
            Read the book
          </Link>
          <AdoptionWizardButton className="btn-secondary text-center">Adoption wizard</AdoptionWizardButton>
        </div>
        <nav
          aria-label="Key kit routes"
          className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm font-semibold text-white/55"
        >
          <Link className="text-aqua transition hover:text-white" href="/use-cases">
            Use cases
          </Link>
          <span className="text-white/25" aria-hidden>
            ·
          </span>
          <Link className="text-aqua transition hover:text-white" href="/tools">
            Tools
          </Link>
          <span className="text-white/25" aria-hidden>
            ·
          </span>
          <Link className="text-aqua transition hover:text-white" href="/workflows">
            Workflows
          </Link>
          <span className="text-white/25" aria-hidden>
            ·
          </span>
          <Link className="text-aqua transition hover:text-white" href="/collections">
            Collections
          </Link>
          <span className="text-white/25" aria-hidden>
            ·
          </span>
          <Link className="text-aqua transition hover:text-white" href="/patterns">
            Patterns
          </Link>
        </nav>
        <div className="mt-14 grid gap-4 sm:grid-cols-3">
          {[
            {
              k: "Proof, not slides",
              v: "Use cases with real screenshots — reference org wiring and how we operate the kit ourselves. Something procurement can open in a browser.",
            },
            {
              k: "One command line",
              v: "The Ship CLI lists patterns, tools, workflows, and collections from the same manifests the site shows — one control surface for operators and automation.",
            },
            {
              k: "Desktop for the business",
              v: "A focused desktop experience for program and business stakeholders is on the roadmap — same playbooks, less terminal.",
            },
          ].map((item) => (
            <div key={item.k} className="glass-panel p-5">
              <p className="font-display text-sm font-bold text-aqua">{item.k}</p>
              <p className="mt-2 text-sm leading-relaxed text-white/65">{item.v}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
