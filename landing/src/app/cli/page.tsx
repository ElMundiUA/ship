import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import Link from "next/link";
import { BookMarkdown } from "@/components/book-content";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { preprocessDocumentationMarkdown } from "@/lib/docs-markdown";
import { repoRoot } from "@/lib/repo-path";

export const metadata: Metadata = {
  title: "shipctl CLI — Ship",
  description:
    "shipctl is Ship's developer workbench: local setup, artifact sync, verification, config, knowledge, and agent rule installation.",
};

function readCliReadme(): string {
  const abs = path.join(repoRoot(), "cli", "README.md");
  if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) {
    throw new Error(`cli/README.md not found at ${abs}`);
  }
  /* The README starts with "# @elmundi/ship-cli" — we render our own
   * landing hero above and trim that first H1 so the page does not
   * stack two big titles next to each other. */
  const body = fs.readFileSync(abs, "utf8");
  return body.replace(/^#\s+@elmundi\/ship-cli\s*\n+/, "");
}

const COMMANDS: { cmd: string; blurb: string }[] = [
  {
    cmd: "shipctl init",
    blurb:
      "Bootstrap repo-level developer setup: config, detected stack, selected artifacts, and agent rule files. --dry-run shows the plan.",
  },
  {
    cmd: "shipctl doctor",
    blurb:
      "Inspect the repo via the adapter registry. Proposes tracker / CI / language / agent values; --write-inventory persists the findings for a later --bootstrap.",
  },
  {
    cmd: "shipctl sync",
    blurb:
      "Fetch the artifacts your config asks for into .ship/cache/. With --lock writes .ship/shipctl.lock.json for reproducibility.",
  },
  {
    cmd: "shipctl verify",
    blurb:
      "Run every check under cli/lib/verify/checks/. Use --no-network in CI to skip methodology / tracker / secret reachability probes.",
  },
  {
    cmd: "shipctl new",
    blurb:
      "Empty-repo path: git init, minimal README, .ship/config.yml from your stack flags, then init --copy-rules in one shot.",
  },
  {
    cmd: "shipctl run",
    blurb:
      "Technical dispatch for configured routines. Use it when the repo-level workflow needs local or CI execution.",
  },
  {
    cmd: "shipctl lanes",
    blurb:
      "Legacy wrapper reconciler for generated .github/workflows/ship-<id>.yml files. Kept for already-seeded repos.",
  },
  {
    cmd: "shipctl callback",
    blurb:
      "Report a terminal status and structured outcome back to Ship so the workspace can show evidence and route decisions.",
  },
  {
    cmd: "shipctl knowledge init",
    blurb:
      "Open a PR that seeds the .ship/knowledge/ starter buckets (code-style, ui-runbook, …) for operators to fill in over time.",
  },
];

export default function CliPage() {
  const md = preprocessDocumentationMarkdown(readCliReadme());

  return (
    <>
      <SiteHeader />
      <main>
        {/* Hero */}
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_90%_60%_at_50%_-10%,rgba(46,230,214,0.18),transparent_55%)]" />
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_50%_40%_at_100%_20%,rgba(255,213,74,0.10),transparent_50%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-sm font-bold uppercase tracking-widest text-aqua">CLI</p>
            <h1 className="font-display mt-4 text-4xl font-bold leading-tight text-white sm:text-5xl">
              Developer setup for a product workspace.
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-aqua/95">shipctl</code> is the only thing
              your repo needs when local control matters. It writes{" "}
              <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-aqua/95">.ship/config.yml</code>, fetches
              versioned artifacts, installs agent rules, and verifies setup without becoming the product entry point.
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <Link href="/getting-started" className="btn-primary inline-flex">
                Product setup
              </Link>
              <a href="#install" className="btn-secondary inline-flex">
                Install
              </a>
              <a href="#commands" className="btn-secondary inline-flex">
                Quick commands
              </a>
              <a href="#reference" className="btn-secondary inline-flex">
                Full reference
              </a>
            </div>
            <p className="mx-auto mt-8 max-w-2xl text-xs uppercase tracking-[0.18em] text-white/45">
              Published as <code className="font-mono text-aqua/80">@elmundi/ship-cli</code> · binary{" "}
              <code className="font-mono text-aqua/80">shipctl</code> · Node 20+
            </p>
          </div>
        </section>

        {/* Install */}
        <section id="install" className="border-b border-white/10 bg-black/30 py-16 sm:py-20">
          <div className="mx-auto max-w-4xl px-4 sm:px-6">
            <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">Install</h2>
            <p className="mt-3 max-w-2xl text-base text-white/65 sm:text-lg">
              Use it once with <code className="rounded bg-white/10 px-1 font-mono text-aqua/90">npx</code> to look around,
              install globally when you want it on PATH.
            </p>
            <div className="mt-6 grid gap-4 sm:grid-cols-2">
              <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/55 p-4 font-mono text-sm text-aqua/95">
                <span className="text-white/40"># one-off</span>
                {"\n"}npx @elmundi/ship-cli help
              </pre>
              <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/55 p-4 font-mono text-sm text-aqua/95">
                <span className="text-white/40"># global</span>
                {"\n"}npm install -g @elmundi/ship-cli{"\n"}shipctl --version
              </pre>
            </div>
            <p className="mt-4 text-sm text-white/55">
              Need to point at a self-hosted methodology API? Set{" "}
              <code className="rounded bg-white/10 px-1 font-mono text-aqua/90">SHIP_API_BASE</code> or pass{" "}
              <code className="rounded bg-white/10 px-1 font-mono text-aqua/90">--base-url</code> on each command. Default
              targets the public methodology host.
            </p>
          </div>
        </section>

        {/* Quick commands */}
        <section id="commands" className="py-16 sm:py-20">
          <div className="mx-auto max-w-4xl px-4 sm:px-6">
            <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">Quick commands</h2>
            <p className="mt-3 max-w-2xl text-base text-white/65 sm:text-lg">
              The verbs developers actually run, grouped roughly Setup → Catalog → Run → Knowledge. Every command
              supports <code className="rounded bg-white/10 px-1 font-mono text-aqua/90">--json</code>,{" "}
              <code className="rounded bg-white/10 px-1 font-mono text-aqua/90">--cwd</code>, and{" "}
              <code className="rounded bg-white/10 px-1 font-mono text-aqua/90">--dry-run</code> where it makes sense.
              The full surface (every flag, every check) lives below or via{" "}
              <code className="rounded bg-white/10 px-1 font-mono text-aqua/90">shipctl help</code>.
            </p>
            <p className="mt-3 text-xs uppercase tracking-[0.18em] text-white/45">
              Product docs use workspace, knowledge, Inbox, automation, and evidence. This page keeps the literal CLI
              names developers need when reviewing files and commands.
            </p>
            <ul className="mt-8 space-y-3">
              {COMMANDS.map((row) => (
                <li
                  key={row.cmd}
                  className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.03] p-4 sm:flex-row sm:items-baseline sm:gap-5"
                >
                  <code className="shrink-0 rounded bg-aqua/10 px-2 py-1 font-mono text-sm font-semibold text-aqua/95 sm:min-w-[12rem]">
                    {row.cmd}
                  </code>
                  <span className="text-sm leading-relaxed text-white/70">{row.blurb}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* Full reference (renders cli/README.md) */}
        <section id="reference" className="border-t border-white/10 bg-black/20 py-16 sm:py-20">
          <div className="mx-auto max-w-4xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-aqua/85">Full reference</p>
            <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">
              <code className="font-mono text-aqua/95">shipctl</code> command surface
            </h2>
            <p className="mt-3 max-w-2xl text-base text-white/65 sm:text-lg">
              Source: <code className="rounded bg-white/10 px-1 font-mono text-aqua/90">cli/README.md</code> in this
              repository. Anything you read here ships with the package on npm.
            </p>
            <article className="book-prose prose prose-invert prose-lg mt-10 max-w-none prose-headings:scroll-mt-28 prose-p:text-white/78 prose-p:leading-relaxed prose-strong:text-white prose-code:text-aqua/90 prose-code:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-md prose-ul:my-5 prose-ol:my-5 prose-li:marker:text-aqua/70">
              <BookMarkdown content={md} />
            </article>
          </div>
        </section>

        {/* Where next */}
        <section className="border-t border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-4xl px-4 text-center sm:px-6">
            <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">Where to go next</h2>
            <div className="mt-8 grid gap-4 sm:grid-cols-3">
              <Link
                href="/getting-started"
                className="group flex flex-col gap-2 rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.04] to-transparent p-5 text-left transition hover:border-aqua/40 hover:bg-white/[0.06]"
              >
                <span className="font-display text-base font-bold text-white group-hover:text-aqua">Getting started</span>
                <p className="text-sm leading-relaxed text-white/60">
                  Start from workspace, repo, tracker, knowledge, dashboard, and Inbox.
                </p>
              </Link>
              <Link
                href="/docs"
                className="group flex flex-col gap-2 rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.04] to-transparent p-5 text-left transition hover:border-aqua/40 hover:bg-white/[0.06]"
              >
                <span className="font-display text-base font-bold text-white group-hover:text-aqua">Docs</span>
                <p className="text-sm leading-relaxed text-white/60">
                  How a real team sequences PRs, lands the first agent, and rolls out across squads.
                </p>
              </Link>
              <Link
                href="/docs/protocol"
                className="group flex flex-col gap-2 rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.04] to-transparent p-5 text-left transition hover:border-aqua/40 hover:bg-white/[0.06]"
              >
                <span className="font-display text-base font-bold text-white group-hover:text-aqua">Protocol RFCs</span>
                <p className="text-sm leading-relaxed text-white/60">
                  Authoritative spec for artifacts, config, telemetry, adapters, and the folder layout.
                </p>
              </Link>
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
