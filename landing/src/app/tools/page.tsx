import type { Metadata } from "next";
import Link from "next/link";
import { ToolsCatalog } from "@/components/tools-catalog";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { repoUrl, shipApiBase } from "@/lib/config";
import { loadToolsManifest } from "@/lib/tools";

export const metadata: Metadata = {
  title: "Tools — Ship",
  description:
    "Linear, GitHub Actions, Playwright, Cursor Cloud Agent, local Chroma, methodology API — concrete surfaces indexed from markdown like org patterns.",
};

export default function ToolsPage() {
  const manifest = loadToolsManifest();

  return (
    <>
      <SiteHeader />
      <main>
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_90%_60%_at_50%_-10%,rgba(255,213,74,0.22),transparent_55%)]" />
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_50%_40%_at_100%_20%,rgba(46,230,214,0.12),transparent_50%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-sun">Tools</p>
            <h1 className="font-display mt-4 text-4xl font-bold leading-tight text-white sm:text-5xl">
              Integrations your stack actually runs
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              This section lists <strong className="text-white">concrete surfaces</strong> — Linear, GitHub Actions, Playwright,
              Cursor Cloud Agent, local Chroma search, the methodology HTTP API, and supporting contracts. Each card opens
              markdown in this repo (same idea as <Link href="/patterns" className="font-semibold text-aqua underline-offset-2 hover:underline">org patterns</Link>
              , but here the focus is <strong className="text-white">tooling</strong>, not SDLC prompt slices).
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <a href="#catalog" className="btn-primary inline-flex">
                Browse tools
              </a>
              <a href="#how" className="btn-secondary inline-flex">
                How to use
              </a>
              <a href="#cli" className="btn-secondary inline-flex">
                CLI
              </a>
              <Link href="/use-cases/elmundi" className="btn-secondary inline-flex">
                Reference org use case
              </Link>
            </div>
          </div>
        </section>

        <section className="py-16 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <div className="grid gap-8 lg:grid-cols-3">
              <div className="glass-panel p-6 sm:p-8">
                <p className="text-xs font-bold uppercase tracking-widest text-aqua">Neutral core</p>
                <h2 className="font-display mt-2 text-xl font-bold text-white">Capabilities, not brands</h2>
                <p className="mt-3 text-sm leading-relaxed text-white/65">
                  Ship standardizes <strong className="text-white/90">interfaces</strong> between tracker, scheduler, agent
                  runtime, regression runner, and security signal. Vendors swap; receipts stay legible.
                </p>
              </div>
              <div className="glass-panel p-6 sm:p-8">
                <p className="text-xs font-bold uppercase tracking-widest text-sun">Git-first</p>
                <h2 className="font-display mt-2 text-xl font-bold text-white">Manifest + markdown</h2>
                <p className="mt-3 text-sm leading-relaxed text-white/65">
                  <code className="text-aqua/90">tools/manifest.json</code> indexes files under{" "}
                  <code className="text-aqua/90">documentation/tools/</code>. Improve the text with normal PRs — the site is a
                  reader.
                </p>
              </div>
              <div className="glass-panel p-6 sm:p-8">
                <p className="text-xs font-bold uppercase tracking-widest text-lilac">Automation</p>
                <h2 className="font-display mt-2 text-xl font-bold text-white">Same HTTP API</h2>
                <p className="mt-3 text-sm leading-relaxed text-white/65">
                  Agents still call <code className="text-aqua/90">POST /search</code> and <code className="text-aqua/90">POST /fetch</code> with repo-relative paths when they need full bodies after discovery.
                </p>
              </div>
            </div>
          </div>
        </section>

        <section id="how" className="border-y border-white/10 bg-black/25 py-16 sm:py-20">
          <div className="mx-auto max-w-3xl px-4 sm:px-6">
            <h2 className="font-display text-center text-3xl font-bold text-white sm:text-4xl">How to use this section</h2>
            <ol className="mt-12 space-y-8 text-white/75">
              <li className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-sun/30 bg-sun/10 font-display font-bold text-sun">
                  1
                </span>
                <div>
                  <p className="font-display font-semibold text-white">Pick the integration you are wiring</p>
                  <p className="mt-1 text-sm leading-relaxed">
                    Start from <strong className="text-white/90">Capability map</strong> if the team argues about scope, then open
                    Linear, Actions, Playwright, or Cursor Cloud for the specifics.
                  </p>
                </div>
              </li>
              <li className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-aqua/30 bg-aqua/10 font-display font-bold text-aqua">
                  2
                </span>
                <div>
                  <p className="font-display font-semibold text-white">Fork and rename for your org</p>
                  <p className="mt-1 text-sm leading-relaxed">
                    ElMundi names in the manual are <strong className="text-white/90">reference only</strong>; your URLs, projects,
                    and secrets differ. The contracts here stay portable.
                  </p>
                </div>
              </li>
              <li className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-lilac/30 bg-lilac/10 font-display font-bold text-lilac">
                  3
                </span>
                <div>
                  <p className="font-display font-semibold text-white">Drive agents from the API + patterns</p>
                  <p className="mt-1 text-sm leading-relaxed">
                    Pair this list with <Link href="/patterns" className="font-semibold text-aqua underline-offset-2 hover:underline">org patterns</Link> and the{" "}
                    <Link href="/docs/tools/backend-api" className="font-semibold text-aqua underline-offset-2 hover:underline">Backend API</Link> doc — search, fetch, then execute role prompts.
                  </p>
                </div>
              </li>
            </ol>
            <p className="mt-12 text-center text-sm text-white/50">
              Story and evidence:{" "}
              <Link className="font-semibold text-aqua underline-offset-2 hover:underline" href="/use-cases/elmundi">
                Use case → ElMundi
              </Link>
              . YAML names and minutes:{" "}
              <Link className="font-semibold text-aqua underline-offset-2 hover:underline" href="/docs/examples/elmundi">
                Manual → ElMundi
              </Link>{" "}
              · on GitHub:{" "}
              <a
                className="font-semibold text-aqua underline-offset-2 hover:underline"
                href={`${repoUrl}/tree/main/tools`}
              >
                tools/manifest.json
              </a>
              .
            </p>
          </div>
        </section>

        <section id="catalog" className="py-16 sm:py-24">
          <div className="mx-auto max-w-6xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-coral">Index</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">Every integration surface</h2>
            <p className="mx-auto mt-4 max-w-2xl text-white/65">
              {manifest.description} Currently <strong className="text-white">{manifest.tools.length}</strong> entries.
            </p>
          </div>
          <div className="mt-12">
            <ToolsCatalog tools={manifest.tools} />
          </div>
        </section>

        <section id="cli" className="border-t border-white/10 bg-gradient-to-b from-white/[0.03] to-transparent py-16 sm:py-24">
          <div className="mx-auto max-w-3xl px-4 sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-aqua">Ship CLI</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white">Tools commands</h2>
            <p className="mt-4 text-sm leading-relaxed text-white/65">
              <strong className="text-white/90">List and show</strong> read <code className="text-aqua/90">tools/manifest.json</code> from disk —{" "}
              <strong className="text-white/90">no API server</strong>. Run from the Ship repo root (or set <code className="text-aqua/90">SHIP_REPO</code>
              ). Semantic search still uses the methodology API — start <code className="text-aqua/90">uvicorn</code> first; base URL defaults to{" "}
              <code className="text-white/80">{shipApiBase}</code> (<code className="text-aqua/90">--base-url</code> / <code className="text-aqua/90">SHIP_API_BASE</code>).
            </p>
            <div className="mt-8 space-y-6 rounded-2xl border border-white/10 bg-black/40 p-5 font-mono text-xs text-white/80 sm:text-sm">
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">List tool ids</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- tools list</pre>
              </div>
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">Print one tool doc (markdown)</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- tools show playwright</pre>
              </div>
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">Semantic search (needs API)</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- docs search &quot;Playwright hosted regression&quot; --top-k 6</pre>
              </div>
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">Fetch arbitrary path (needs API)</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- docs fetch documentation/tools/integrations/playwright.md</pre>
              </div>
            </div>
            <p className="mt-8 text-center text-sm text-white/50">
              Raw HTTP:{" "}
              <Link href="/docs/tools/backend-api" className="font-semibold text-aqua underline-offset-2 hover:underline">
                Manual → Backend API
              </Link>
              {" · "}
              <Link href="/patterns#cli" className="font-semibold text-aqua underline-offset-2 hover:underline">
                Patterns CLI
              </Link>
              {" · "}
              <Link href="/#api" className="font-semibold text-aqua underline-offset-2 hover:underline">
                Companion API
              </Link>
            </p>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
