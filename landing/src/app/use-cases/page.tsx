import type { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Use cases — Ship",
  description:
    "Enterprise-style success stories: reference org wiring (ElMundi) and how the Ship open methodology kit is operated as a product.",
};

const CASES = [
  {
    slug: "elmundi",
    title: "ElMundi — reference deployment",
    summary:
      "Public monorepo wiring: Linear + GitHub Actions + Cursor Cloud Agent, SDLC grid, audits, hosted Playwright — receipts you can diff.",
    tags: ["Linear", "GitHub Actions", "Cursor Cloud", "Playwright"],
  },
  {
    slug: "ship",
    title: "Ship — open methodology kit",
    summary:
      "Instruction-first docs, patterns catalog, tools, workflows, and agent API — shipped as one Next.js surface with the same discipline we preach.",
    tags: ["Docs", "Patterns", "API", "Apache-2.0"],
  },
];

export default function UseCasesIndexPage() {
  return (
    <>
      <SiteHeader />
      <main>
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-10%,rgba(179,136,255,0.2),transparent_55%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-lilac/90">Use cases</p>
            <h1 className="font-display mt-4 text-4xl font-bold text-white sm:text-5xl">Stories written like enterprise proof</h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              Each case follows a familiar arc — <strong className="text-white">challenge</strong>,{" "}
              <strong className="text-white">solution</strong>, <strong className="text-white">implementation</strong>,{" "}
              <strong className="text-white">outcomes</strong>, and <strong className="text-white">evidence</strong> with
              screenshots from this site. Technical receipts (YAML names, cron tables) still live in the manual where they
              belong.
            </p>
          </div>
        </section>

        <section className="py-16 sm:py-20">
          <div className="mx-auto grid max-w-5xl gap-8 px-4 sm:px-6 md:grid-cols-2">
            {CASES.map((c) => (
              <Link
                key={c.slug}
                href={`/use-cases/${c.slug}`}
                className="group flex flex-col rounded-3xl border border-white/12 bg-gradient-to-br from-white/[0.08] via-white/[0.02] to-transparent p-8 shadow-card transition hover:border-aqua/35 hover:shadow-glow"
              >
                <h2 className="font-display text-2xl font-bold text-white group-hover:text-aqua">{c.title}</h2>
                <p className="mt-4 flex-1 text-sm leading-relaxed text-white/65">{c.summary}</p>
                <div className="mt-6 flex flex-wrap gap-2">
                  {c.tags.map((t) => (
                    <span
                      key={t}
                      className="rounded-full border border-white/10 bg-black/30 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-white/50"
                    >
                      {t}
                    </span>
                  ))}
                </div>
                <span className="mt-8 text-sm font-semibold text-aqua">Read use case →</span>
              </Link>
            ))}
          </div>
        </section>

        <section className="border-t border-white/10 bg-black/25 py-12">
          <div className="mx-auto max-w-3xl px-4 text-center sm:px-6">
            <p className="text-sm text-white/55">
              Index preview (same chrome as the rest of the site) is also archived as an asset for decks.
            </p>
            <div className="mx-auto mt-8 max-w-4xl overflow-hidden rounded-2xl border border-white/10 bg-black/40">
              <Image
                src="/use-cases/use-cases-index.png"
                alt="Use cases index in the Ship Next.js app"
                width={1400}
                height={900}
                className="h-auto w-full object-cover object-top"
              />
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
