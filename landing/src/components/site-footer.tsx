import Link from "next/link";
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
              Apache-2.0 product delivery workspace for solo founders, product owners, and engineers: one place for
              policies, decisions, knowledge, and evidence you can audit. The public vocabulary is documented under{" "}
              <Link className="text-aqua hover:text-white" href="/docs/concepts">Concepts</Link>.
            </p>
          </div>

          <div className="grid gap-10 sm:col-span-2 sm:grid-cols-2 lg:col-span-8 lg:grid-cols-3">
            <div className="space-y-4">
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-white/40">Evaluate</p>
              <nav className="flex flex-col gap-3 text-base">
                <Link className="text-white/70 transition hover:text-aqua" href="/use-cases">
                  Use cases
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/docs">
                  Get started
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/getting-started">
                  Workspace setup
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/#team">
                  Team
                </Link>
              </nav>
            </div>

            <div className="space-y-4">
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-white/40">Workspace</p>
              <nav className="flex flex-col gap-3 text-base">
                <Link className="text-white/70 transition hover:text-aqua" href="/kit">
                  Product surfaces
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/patterns">
                  Policies
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/docs/knowledge-buckets">
                  Knowledge
                </Link>
              </nav>
            </div>

            <div className="space-y-4 sm:col-span-2 lg:col-span-1">
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-white/40">Reference</p>
              <nav className="flex flex-col gap-3 text-base">
                <Link className="text-white/70 transition hover:text-aqua" href="/book">
                  The book
                </Link>
                <Link className="text-white/70 transition hover:text-aqua" href="/blog">
                  Ship Log (blog)
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
          <div className="flex flex-wrap items-center justify-between gap-4">
            <p className="text-sm text-white/50">
              © {YEAR} <span className="text-white/80">Denys Kuzin</span>. Documentation and site content are part of the Ship
              open-source project (Apache-2.0).
            </p>
            <Link
              href="/docs/protocol"
              className="inline-flex items-center gap-2 rounded-full border border-aqua/30 bg-aqua/[0.08] px-3 py-1 text-xs font-bold uppercase tracking-[0.16em] text-aqua transition hover:border-aqua/60 hover:bg-aqua/[0.14]"
              aria-label="Protocol v1 — read the RFC index"
            >
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-aqua" aria-hidden />
              Protocol v1
            </Link>
          </div>
        </div>
      </div>
    </footer>
  );
}
