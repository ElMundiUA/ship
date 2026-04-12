import Link from "next/link";
import { AdoptionWizardButton } from "@/components/adoption-wizard";
import { repoUrl } from "@/lib/config";

const YEAR = new Date().getFullYear();

export function SiteFooter() {
  return (
    <footer className="border-t border-white/10 bg-gradient-to-b from-black/50 to-black/70 py-20 sm:py-24 lg:py-28">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <div className="grid gap-12 sm:grid-cols-2 lg:grid-cols-12 lg:gap-10">
          <div className="lg:col-span-4">
            <p className="font-display text-2xl font-bold tracking-tight text-white sm:text-3xl">
              Ship<span className="text-aqua">.</span>
            </p>
            <p className="mt-2 font-display text-sm font-semibold tracking-wide text-aqua/90">Ship ships Ship.</p>
            <p className="mt-2 max-w-md text-sm leading-relaxed text-white/45">
              The framework, the delivery verb, the thing in production — same word, three beats.
            </p>
            <p className="mt-5 max-w-md text-base leading-relaxed text-white/60">
              Apache-2.0 methodology kit: one site where buyers and engineers read the same story — docs, patterns,
              workflows, integrations, and use cases you can audit.
            </p>
            <AdoptionWizardButton className="mt-6 text-left text-sm font-semibold text-aqua underline-offset-4 hover:underline">
              Open adoption wizard
            </AdoptionWizardButton>
          </div>

          <div className="grid gap-10 sm:col-span-2 sm:grid-cols-2 lg:col-span-8 lg:grid-cols-3">
            <div className="space-y-4">
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-white/40">Evaluate</p>
              <nav className="flex flex-col gap-3 text-base">
                <Link className="text-white/70 transition hover:text-aqua" href="/docs/getting-started">
                  Getting started
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/use-cases">
                  Use cases
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/docs/adoption">
                  Adoption
                </Link>
              </nav>
            </div>

            <div className="space-y-4">
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-white/40">Product</p>
              <nav className="flex flex-col gap-3 text-base">
                <Link className="text-white/70 transition hover:text-aqua" href="/patterns">
                  Org patterns
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/workflows">
                  Workflows
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/collections">
                  Collections
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/tools">
                  Tools &amp; integrations
                </Link>
              </nav>
            </div>

            <div className="space-y-4 sm:col-span-2 lg:col-span-1">
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-white/40">Reference</p>
              <nav className="flex flex-col gap-3 text-base">
                <Link className="text-white/70 transition hover:text-aqua" href="/docs">
                  Manual
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/book">
                  The book
                </Link>
                <a className="text-white/70 transition hover:text-aqua" href={repoUrl} target="_blank" rel="noreferrer">
                  GitHub
                </a>
                <a
                  className="text-white/70 transition hover:text-aqua"
                  href={`${repoUrl}/blob/main/LICENSE`}
                  target="_blank"
                  rel="noreferrer"
                >
                  License
                </a>
              </nav>
            </div>
          </div>
        </div>

        <div className="mt-16 border-t border-white/10 pt-10 sm:mt-20 sm:pt-12">
          <p className="text-center text-sm text-white/50 sm:text-left">
            © {YEAR} <span className="text-white/80">Denys Kuzin</span>. Documentation and site content are part of the Ship
            open-source project (Apache-2.0).
          </p>
        </div>
      </div>
    </footer>
  );
}
