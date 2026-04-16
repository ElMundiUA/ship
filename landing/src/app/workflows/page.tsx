import type { Metadata } from "next";
import Link from "next/link";
import { WorkflowsCatalog } from "@/components/workflows-catalog";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { repoUrl, shipApiBase } from "@/lib/config";
import { loadWorkflowsManifest } from "@/lib/workflows";

export const metadata: Metadata = {
  title: "Workflows — Ship",
  description:
    "Scheduler intents: SDLC cadence, PR gates, hosted E2E, pipeline self-heal, and parallel audits — behavioural names you map to YAML in your org.",
};

export default function WorkflowsPage() {
  const manifest = loadWorkflowsManifest();

  return (
    <>
      <SiteHeader />
      <main>
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_90%_60%_at_50%_-10%,rgba(255,92,108,0.22),transparent_55%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-coral">Workflows</p>
            <h1 className="font-display mt-4 text-4xl font-bold leading-tight text-white sm:text-5xl">
              Pipeline behaviour, not YAML brand names
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              Each card is an <strong className="text-white">intent</strong> you implement in GitHub Actions, GitLab, or another scheduler—paired with{" "}
              <Link href="/tools" className="font-semibold text-coral underline-offset-2 hover:underline">tools</Link>,{" "}
              <Link href="/patterns" className="font-semibold text-coral underline-offset-2 hover:underline">patterns</Link>, and{" "}
              <Link href="/collections" className="font-semibold text-coral underline-offset-2 hover:underline">collections</Link> that bundle them for real product shapes.
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <a href="#catalog" className="btn-primary inline-flex">
                Browse workflows
              </a>
              <a href="#cli" className="btn-secondary inline-flex">
                CLI
              </a>
              <Link href="/docs/prompts-workflows" className="btn-secondary inline-flex">
                Prompts &amp; workflows (manual)
              </Link>
              <Link href="/use-cases/elmundi" className="btn-secondary inline-flex">
                Reference org use case
              </Link>
            </div>
          </div>
        </section>

        <section className="py-12 sm:py-16">
          <div className="mx-auto max-w-3xl px-4 text-center text-sm text-white/55 sm:px-6">
            <p>
              Narrative and screenshots:{" "}
              <Link href="/use-cases/elmundi" className="font-semibold text-coral underline-offset-2 hover:underline">
                Use case → ElMundi
              </Link>
              . Filenames and cron tables:{" "}
              <Link href="/docs/examples/elmundi" className="font-semibold text-coral underline-offset-2 hover:underline">
                Manual → ElMundi
              </Link>
              . Manifest source:{" "}
              <a className="font-semibold text-coral underline-offset-2 hover:underline" href={`${repoUrl}/tree/main/workflows`}>
                workflows/manifest.json
              </a>
              .
            </p>
          </div>
        </section>

        <section id="catalog" className="py-12 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-coral">Index</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">All scheduler intents</h2>
            <p className="mx-auto mt-4 max-w-2xl text-white/65">
              {manifest.description}{" "}
              <strong className="text-white">{manifest.workflows.length}</strong> entries.
            </p>
          </div>
          <div className="mt-12">
            <WorkflowsCatalog workflows={manifest.workflows} />
          </div>
        </section>

        <section id="cli" className="border-t border-white/10 bg-gradient-to-b from-white/[0.03] to-transparent py-16 sm:py-24">
          <div className="mx-auto max-w-3xl px-4 sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-coral">Ship CLI</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white">Workflows commands</h2>
            <p className="mt-4 text-sm leading-relaxed text-white/65">
              Reads <code className="text-aqua/90">workflows/manifest.json</code> from disk — no API. From the Ship repo root (or{" "}
              <code className="text-aqua/90">SHIP_REPO</code>). Explore intent text with <code className="text-aqua/90">ship search</code> when the API is up (
              <code className="text-white/80">{shipApiBase}</code>).
            </p>
            <div className="mt-8 space-y-6 rounded-2xl border border-white/10 bg-black/40 p-5 font-mono text-xs text-white/80 sm:text-sm">
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">List workflow ids</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- workflow list</pre>
              </div>
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">Print one intent (markdown)</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- workflow show pr-and-ci-gate</pre>
              </div>
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">Semantic search (needs API)</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">npm run ship -- search &quot;PR gate preview&quot; --top-k 6</pre>
              </div>
            </div>
            <p className="mt-8 text-center text-sm text-white/50">
              <Link href="/tools#cli" className="font-semibold text-coral underline-offset-2 hover:underline">
                Tools CLI
              </Link>
              {" · "}
              <Link href="/collections#cli" className="font-semibold text-coral underline-offset-2 hover:underline">
                Collections CLI
              </Link>
              {" · "}
              <Link href="/docs/tools/backend-api" className="font-semibold text-coral underline-offset-2 hover:underline">
                Backend API
              </Link>
            </p>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
