import type { Metadata } from "next";
import Link from "next/link";
import { CollectionsCatalog } from "@/components/collections-catalog";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { repoUrl, shipApiBase } from "@/lib/config";
import { loadCollectionsManifest } from "@/lib/collections";

export const metadata: Metadata = {
  title: "Collections — Ship",
  description:
    "Collections — curated bundles of patterns (Plays), tools, and rules for common product shapes (web app, API backend, mobile app, monorepo).",
};

export default function CollectionsPage() {
  const manifest = loadCollectionsManifest();

  return (
    <>
      <SiteHeader />
      <main>
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_90%_60%_at_50%_-10%,rgba(179,136,255,0.28),transparent_55%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-lilac">Collections</p>
            <h1 className="font-display mt-4 text-4xl font-bold leading-tight text-white sm:text-5xl">
              One page: tools + patterns
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              A <strong className="text-white">collection</strong> is a curated bundle for a product shape—tables of links into{" "}
              <Link href="/tools" className="font-semibold text-lilac underline-offset-2 hover:underline">tools</Link> and{" "}
              <Link href="/patterns" className="font-semibold text-lilac underline-offset-2 hover:underline">patterns</Link>, plus docs pages when you need prose.
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <a href="#catalog" className="btn-primary inline-flex">
                Browse collections
              </a>
              <a href="#cli" className="btn-secondary inline-flex">
                CLI
              </a>
              <Link href="/patterns" className="btn-secondary inline-flex">
                Patterns
              </Link>
              <Link href="/getting-started" className="btn-secondary inline-flex">
                Getting started
              </Link>
            </div>
          </div>
        </section>

        <section className="py-12 sm:py-16">
          <div className="mx-auto max-w-3xl px-4 text-center text-sm text-white/55 sm:px-6">
            <p>
              Add or edit bundles in{" "}
              <a className="font-semibold text-lilac underline-offset-2 hover:underline" href={`${repoUrl}/tree/main/artifacts/collections`}>
                artifacts/collections/&lt;id&gt;/ARTIFACT.md
              </a>{" "}
              — YAML frontmatter plus body in one file, reviewed in normal PRs.
            </p>
          </div>
        </section>

        <section id="catalog" className="py-12 sm:py-20">
          <div className="mx-auto max-w-6xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-lilac">Bundles</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">Pick a shape</h2>
            <p className="mx-auto mt-4 max-w-2xl text-white/65">
              {manifest.description} <strong className="text-white">{manifest.collections.length}</strong> bundles.
            </p>
          </div>
          <div className="mt-12">
            <CollectionsCatalog collections={manifest.collections} />
          </div>
        </section>

        <section id="cli" className="border-t border-white/10 bg-gradient-to-b from-white/[0.03] to-transparent py-16 sm:py-24">
          <div className="mx-auto max-w-3xl px-4 sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-lilac">Ship CLI</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white">Collections commands</h2>
            <p className="mt-4 text-sm leading-relaxed text-white/65">
              Scans <code className="text-aqua/90">artifacts/collections/&lt;id&gt;/ARTIFACT.md</code> on disk — no API. From the Ship repo root (or with{" "}
              <code className="text-aqua/90">SHIP_REPO</code> set). Cross-link discovery: <code className="text-aqua/90">shipctl search</code> when the API is running (
              <code className="text-white/80">{shipApiBase}</code>).
            </p>
            <div className="mt-8 space-y-6 rounded-2xl border border-white/10 bg-black/40 p-5 font-mono text-xs text-white/80 sm:text-sm">
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">List bundle ids</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">shipctl collection list</pre>
              </div>
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">Print one bundle (markdown)</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">shipctl collection show web-application</pre>
              </div>
              <div>
                <p className="mb-2 text-[10px] font-bold uppercase tracking-widest text-white/40">Semantic search (needs API)</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">shipctl search &quot;collections bundle web app&quot; --top-k 6</pre>
              </div>
            </div>
            <p className="mt-8 text-center text-sm text-white/50">
              <Link href="/tools#cli" className="font-semibold text-lilac underline-offset-2 hover:underline">
                Tools CLI
              </Link>
              {" · "}
              <Link href="/docs/tools/backend-api" className="font-semibold text-lilac underline-offset-2 hover:underline">
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
