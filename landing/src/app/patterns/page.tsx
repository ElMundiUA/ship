import type { Metadata } from "next";
import Link from "next/link";
import { PatternsCatalog } from "@/components/patterns-catalog";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { repoUrl, shipApiBase } from "@/lib/config";
import { loadPatternsManifest } from "@/lib/patterns";

export const metadata: Metadata = {
  title: "Org patterns — Ship",
  description:
    "Reviewable markdown prompts for agents: onboarding, scheduled cloud roles, and lane playbooks — discover bodies with the Ship CLI (pattern list/show/fetch).",
};

export default function PatternsPage() {
  const manifest = loadPatternsManifest();

  return (
    <>
      <SiteHeader />
      <main>
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_90%_60%_at_50%_-10%,rgba(179,136,255,0.35),transparent_55%)]" />
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_50%_40%_at_100%_20%,rgba(255,213,74,0.12),transparent_50%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-lilac">Org patterns</p>
            <h1 className="font-display mt-4 text-4xl font-bold leading-tight text-white sm:text-5xl">
              Small instructions that scale with the org
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              Patterns are not vendor &ldquo;skills&rdquo;. They are <strong className="text-white">versioned markdown</strong> in
              this repo — onboarding playbooks, scheduled cloud roles, and lane playbooks (seeded from the reference
              org stack). Agents adapt the text to Cursor, Cloud Code, or whatever runtime you run.
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <a href="#catalog" className="btn-primary inline-flex">
                Browse patterns
              </a>
              <a href="#how" className="btn-secondary inline-flex">
                How to use
              </a>
              <a href="#cli" className="btn-secondary inline-flex">
                CLI
              </a>
            </div>
          </div>
        </section>

        <section className="py-16 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <div className="grid gap-8 lg:grid-cols-3">
              <div className="glass-panel p-6 sm:p-8">
                <p className="text-xs font-bold uppercase tracking-widest text-aqua">Why</p>
                <h2 className="font-display mt-2 text-xl font-bold text-white">Distributed improvements</h2>
                <p className="mt-3 text-sm leading-relaxed text-white/65">
                  One manifest points at files under <code className="text-aqua/90">prompts/</code>. Merge review is your
                  moderation gate: proposals land as normal PRs, then every project that pins this repo sees the update.
                </p>
              </div>
              <div className="glass-panel p-6 sm:p-8">
                <p className="text-xs font-bold uppercase tracking-widest text-sun">Neutral shape</p>
                <h2 className="font-display mt-2 text-xl font-bold text-white">Runtime-agnostic</h2>
                <p className="mt-3 text-sm leading-relaxed text-white/65">
                  No generator layer: agents already reshape markdown for their host. You keep one canonical source; each
                  runtime carries its own adapter habits.
                </p>
              </div>
              <div className="glass-panel p-6 sm:p-8">
                <p className="text-xs font-bold uppercase tracking-widest text-lilac">Discovery</p>
                <h2 className="font-display mt-2 text-xl font-bold text-white">List + body API</h2>
                <p className="mt-3 text-sm leading-relaxed text-white/65">
                  Pair <code className="text-aqua/90">ship pattern list</code> with <code className="text-aqua/90">ship search</code> when you need fuzzy match; use <code className="text-aqua/90">ship pattern show &lt;id&gt;</code> or <code className="text-aqua/90">ship pattern fetch &lt;id&gt;</code> when you already know the id.
                </p>
              </div>
            </div>
          </div>
        </section>

        <section id="how" className="border-y border-white/10 bg-black/25 py-16 sm:py-20">
          <div className="mx-auto max-w-3xl px-4 sm:px-6">
            <h2 className="font-display text-center text-3xl font-bold text-white sm:text-4xl">How teams use this</h2>
            <ol className="mt-12 space-y-8 text-white/75">
              <li className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-aqua/30 bg-aqua/10 font-display font-bold text-aqua">
                  1
                </span>
                <div>
                  <p className="font-display font-semibold text-white">Pin the repo (or subtree)</p>
                  <p className="mt-1 text-sm leading-relaxed">
                    Each pattern lives at <code className="text-aqua/90">artifacts/patterns/&lt;id&gt;/ARTIFACT.md</code> with
                    YAML frontmatter as the single source of truth. Your fork owns the folder; upstream Ship can be merged
                    like any other dependency.
                  </p>
                </div>
              </li>
              <li className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-lilac/30 bg-lilac/10 font-display font-bold text-lilac">
                  2
                </span>
                <div>
                  <p className="font-display font-semibold text-white">Give agents a standing policy</p>
                  <p className="mt-1 text-sm leading-relaxed">
                    In your root agent instructions: before a task, call the methodology API — list patterns, optionally
                    search, fetch the body, compare to what the workspace already cached, then adapt locally.
                  </p>
                </div>
              </li>
              <li className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-sun/30 bg-sun/10 font-display font-bold text-sun">
                  3
                </span>
                <div>
                  <p className="font-display font-semibold text-white">Change through PRs only</p>
                  <p className="mt-1 text-sm leading-relaxed">
                    Agents may open branches and propose edits; humans merge. That keeps the pattern set{' '}
                    <strong className="text-white/90">moderated</strong> without banning automation from the author seat.
                  </p>
                </div>
              </li>
            </ol>
            <p className="mt-12 text-center text-sm text-white/50">
              Operational detail for ElMundi-style wiring lives in{" "}
              <a className="font-semibold text-aqua underline-offset-2 hover:underline" href={`${repoUrl}/tree/main/documentation/examples/elmundi`}>
                documentation/examples/elmundi
              </a>
              .
            </p>
          </div>
        </section>

        <section id="catalog" className="py-16 sm:py-24">
          <div className="mx-auto max-w-6xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-coral">Index</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">Every pattern in this repo</h2>
            <p className="mx-auto mt-4 max-w-2xl text-white/65">
              {manifest.description} Currently <strong className="text-white">{manifest.patterns.length}</strong> entries.
            </p>
          </div>
          <div className="mt-12">
            <PatternsCatalog patterns={manifest.patterns} />
          </div>
        </section>

        <section id="cli" className="border-t border-white/10 bg-gradient-to-b from-white/[0.03] to-transparent py-16 sm:py-24">
          <div className="mx-auto max-w-3xl px-4 sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-aqua">Ship CLI</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white">Patterns commands</h2>
            <p className="mt-4 text-sm leading-relaxed text-white/65">
              Run these from the <strong className="text-white/90">Ship repository root</strong> (where <code className="text-aqua/90">package.json</code> defines{" "}
              <code className="text-aqua/90">npm run ship</code>). The CLI talks to the same methodology API as agents — start{" "}
              <code className="text-aqua/90">uvicorn</code> first (see manual). API base defaults to{" "}
              <code className="text-white/80">{shipApiBase}</code>; override with <code className="text-aqua/90">--base-url</code> or{" "}
              <code className="text-aqua/90">SHIP_API_BASE</code>.
            </p>
            <div className="mt-8 space-y-6 rounded-2xl border border-white/10 bg-black/40 p-5 font-mono text-xs text-white/80 sm:text-sm">
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">List pattern ids</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- pattern list</pre>
              </div>
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">Print one pattern (markdown body)</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- pattern show catalog-a1-intake</pre>
              </div>
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">Semantic search (optional)</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- search &quot;intake idempotency Todo&quot; --top-k 6</pre>
              </div>
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">Machine-readable JSON</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- pattern list --json</pre>
              </div>
            </div>
            <p className="mt-8 text-center text-sm text-white/50">
              Other manifest CLIs:{" "}
              <Link href="/tools#cli" className="font-semibold text-aqua underline-offset-2 hover:underline">
                Tools
              </Link>
              {" · "}
              <Link href="/workflows#cli" className="font-semibold text-aqua underline-offset-2 hover:underline">
                Workflows
              </Link>
              {" · "}
              <Link href="/collections#cli" className="font-semibold text-aqua underline-offset-2 hover:underline">
                Collections
              </Link>
              . HTTP for agents and CI (<code className="text-white/60">GET /patterns</code>, <code className="text-white/60">POST /search</code>, …):{" "}
              <Link href="/#api" className="font-semibold text-aqua underline-offset-2 hover:underline">
                Companion API
              </Link>
              {" · "}
              <Link className="font-semibold text-aqua underline-offset-2 hover:underline" href="/docs/tools/backend-api">
                Manual → Backend API
              </Link>{" "}
              (
              <a className="text-aqua/80 underline-offset-2 hover:underline" href={`${repoUrl}/blob/main/artifacts/tools/methodology-api/ARTIFACT.md`}>
                source on GitHub
              </a>
              ).
            </p>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
