import type { Metadata } from "next";
import Link from "next/link";
import { AgentSetupForm } from "@/components/agent-setup-form";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Getting started — Ship",
  description:
    "Pick an adoption path, generate the exact shipctl init command, and prompt your agent. Three paths: existing repo, greenfield, or verify-only.",
};

const STACK_EXPECTATIONS: { label: string; body: string }[] = [
  {
    label: "Tracker",
    body: "A queue state equivalent to Todo and execution states equivalent to In Progress, In Review, Done, Blocked.",
  },
  {
    label: "Routing signals",
    body: "Fields where ready:*, stage:*, result:* (or your equivalent) can be set so automation has something deterministic to match.",
  },
  {
    label: "Evidence trail",
    body: "Comments, links and reports attached to the ticket — never to a parallel chat thread.",
  },
  {
    label: "CI surface",
    body: "Pipelines that can run lint → build → test → e2e → delivery → release. Names are yours; stages are non-negotiable.",
  },
  {
    label: "Secret store",
    body: "Secrets handled by the platform's secret manager. Never in .ship/config.yml, never in prompts.",
  },
  {
    label: "Agent footprint",
    body: "Any of the 13 supported agents with on-disk markers shipctl can detect, or one you teach via collection/agent-rules-*.",
  },
  {
    label: "Provenance",
    body: "A way to record <kind>:<id>@<version> for every consumed artifact in the PR description.",
  },
  {
    label: "Promotion owner",
    body: "A named approver (or scheduled gate) for promotion to production.",
  },
];

const DAY_TWO: { cmd: string; body: string }[] = [
  { cmd: "shipctl doctor", body: "Re-infer the stack from on-disk signals; persist with --write-inventory." },
  { cmd: "shipctl sync", body: "Refresh the cache from the methodology API; honours artifacts.pins." },
  { cmd: "shipctl verify", body: "Full local + network checks. Add --no-network for offline / CI runs." },
  { cmd: "shipctl config get|set|show", body: "Atomic edits on .ship/config.yml. Never mutate the file by hand." },
  { cmd: "shipctl feedback", body: "Capture a short structured report when an artifact bites you." },
];

const NEXT_LINKS: { href: string; title: string; body: string }[] = [
  {
    href: "/cli",
    title: "shipctl CLI reference",
    body: "Every command, every flag, every check the CLI runs.",
  },
  {
    href: "/docs/concepts",
    title: "Concepts",
    body: "Vocabulary: artifact, kind, channel, pin, install_target, adapter, preset, marker.",
  },
  {
    href: "/docs/configuration",
    title: "Configuration",
    body: "Every field of .ship/config.yml plus the on-disk layout.",
  },
  {
    href: "/docs/operating",
    title: "Operating",
    body: "Day-2 recipes: pin a version, switch channels, read verify, debug sync.",
  },
  {
    href: "/docs/discovery",
    title: "Discovery contract",
    body: "The Phase 0–4 interview an agent runs before its first PR.",
  },
  {
    href: "/docs/troubleshooting",
    title: "Troubleshooting",
    body: "Failure-first lookup: symptom → likely cause → fix.",
  },
  {
    href: "/tools",
    title: "Tools & integrations",
    body: "Tracker adapters, CI adapters, secret backends, and the rest of the catalog.",
  },
  {
    href: "/book",
    title: "The book — long rationale",
    body: "Why the loop looks the way it does. Read in order if you have an evening.",
  },
];

export default function GettingStartedPage() {
  return (
    <div className="min-h-screen bg-ink pt-16">
      <SiteHeader />
      <main className="mx-auto max-w-5xl px-4 py-10 sm:px-6 sm:py-12 lg:py-16">
        <header className="docs-hero">
          <p className="docs-hero-kicker">Setup wizard</p>
          <h1 className="docs-hero-title">Getting started</h1>
          <p className="docs-hero-lede">
            Everything Ship does in your repo runs through one CLI binary —{" "}
            <code className="rounded-md border border-white/10 bg-white/[0.06] px-1.5 py-0.5 font-mono text-[0.9em] text-aqua/95">shipctl</code> — and one config
            file — <code className="rounded-md border border-white/10 bg-white/[0.06] px-1.5 py-0.5 font-mono text-[0.9em] text-aqua/95">.ship/config.yml</code>.
            Pick the adoption path that matches the repo you are sitting in, fill the wizard below, paste the command into
            your terminal, and paste the prompt into your agent. You are done.
          </p>
        </header>

        <section className="mt-12">
          <div className="mb-4 flex items-baseline justify-between gap-4">
            <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">
              1 · Build your{" "}
              <code className="rounded bg-white/10 px-2 py-0.5 font-mono text-aqua/90">shipctl</code> command
            </h2>
            <span className="hidden text-xs font-semibold uppercase tracking-[0.16em] text-white/45 sm:inline">
              Generated locally · nothing leaves your browser
            </span>
          </div>
          <AgentSetupForm />
        </section>

        <section className="mt-16">
          <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">2 · Three adoption paths</h2>
          <p className="mt-3 max-w-3xl text-base text-white/65 sm:text-lg">
            The wizard above writes one of these three commands. The differences below tell you which mode the wizard is
            in and why.
          </p>

          <div className="mt-8 grid gap-5 lg:grid-cols-3">
            <PathCard
              tag="init"
              title="Existing repo"
              blurb="Most teams. Run from the root of the repo you want agents to operate on."
              command={`npx @elmundi/ship-cli init --yes \\
  --agents cursor,codex,claude-md \\
  --tracker linear --ci gh-actions --preset web-app \\
  --copy-rules`}
              footer={
                <>
                  <code className="font-mono text-aqua/90">shipctl init</code> writes{" "}
                  <code className="font-mono text-aqua/90">.ship/config.yml</code> (RFC-0002), seeds the cache, installs
                  per-agent rule files at the install targets declared in each{" "}
                  <code className="font-mono text-aqua/90">collection/agent-rules-&lt;agent&gt;</code> artifact, and stops
                  short of CI/tracker scaffolding. Add{" "}
                  <code className="font-mono text-aqua/90">--bootstrap</code> for the supported{" "}
                  <code className="font-mono">mobile-app + gh-actions + linear</code> skeleton (others get a{" "}
                  <code className="font-mono">SHIP_BOOTSTRAP_PLAN.md</code>).
                </>
              }
            />
            <PathCard
              tag="new"
              title="Greenfield"
              blurb="Empty directory, brand-new product. Scaffolds git, README, config, and agent rules in one go."
              command={`npx @elmundi/ship-cli new my-product \\
  --preset web-app --tracker linear --ci gh-actions \\
  --agents cursor,codex --yes
cd my-product`}
              footer={
                <>
                  <code className="font-mono text-aqua/90">shipctl new</code> runs{" "}
                  <code className="font-mono">git init</code>, drops a minimal README, seeds{" "}
                  <code className="font-mono text-aqua/90">.ship/config.yml</code>, and runs{" "}
                  <code className="font-mono">init --copy-rules</code> for the listed agents. Use{" "}
                  <code className="font-mono text-aqua/90">--here</code> to scaffold into the current directory instead of
                  creating <code className="font-mono">&lt;name&gt;/</code>.
                </>
              }
            />
            <PathCard
              tag="verify"
              title="Quick verify"
              blurb="Anywhere with a .ship/config.yml in place. Useful in CI or as a smoke test after a sync."
              command={`npx @elmundi/ship-cli verify --no-network`}
              footer={
                <>
                  Runs every check under{" "}
                  <code className="font-mono text-aqua/90">cli/lib/verify/checks/</code>: config schema, gitignore,
                  rule markers, cache integrity, bootstrap markers, declared-agent disk signals.{" "}
                  <code className="font-mono text-aqua/90">--no-network</code> skips the methodology / Linear / secret
                  reachability probes for offline runs.
                </>
              }
            />
          </div>
        </section>

        <section className="mt-16">
          <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">3 · What Ship expects from any stack</h2>
          <p className="mt-3 max-w-3xl text-base text-white/65 sm:text-lg">
            Ship is opinionated about the <em>shape</em> of your delivery system, not the brand names. If your tools cover
            the rows below, the methodology fits. Adapters in{" "}
            <code className="rounded bg-white/10 px-1 font-mono text-aqua/90">.ship/config.yml</code> bind the brand names
            to those shapes (RFC-0004).
          </p>

          <ul className="mt-8 grid gap-3 sm:grid-cols-2">
            {STACK_EXPECTATIONS.map((row) => (
              <li
                key={row.label}
                className="rounded-2xl border border-white/10 bg-white/[0.03] p-5"
              >
                <p className="font-display text-base font-bold text-white">{row.label}</p>
                <p className="mt-2 text-sm leading-relaxed text-white/65">{row.body}</p>
              </li>
            ))}
          </ul>
        </section>

        <section className="mt-16">
          <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">4 · After init: keep the loop tight</h2>
          <p className="mt-3 max-w-3xl text-base text-white/65 sm:text-lg">
            Day two is five commands you actually run. Each one prints what it would do; none of them mutates remote
            state without consent.
          </p>
          <p className="mt-4 max-w-3xl rounded-xl border border-aqua/20 bg-aqua/[0.05] px-4 py-3 text-sm leading-relaxed text-white/75">
            <span className="font-semibold uppercase tracking-[0.16em] text-aqua/85">Day two — operator surfaces.</span>
            {" "}Once your first sync completes: open the <strong className="text-white">Inbox</strong> for what
            needs you, browse <strong className="text-white">Plays</strong> for the catalog, see{" "}
            <strong className="text-white">Coverage</strong> to spot gaps, and check{" "}
            <strong className="text-white">Runs</strong> for what just happened.
          </p>
          <ul className="mt-8 space-y-3">
            {DAY_TWO.map((row) => (
              <li
                key={row.cmd}
                className="flex flex-col gap-2 rounded-xl border border-white/10 bg-black/35 p-4 sm:flex-row sm:items-baseline sm:gap-5"
              >
                <code className="shrink-0 rounded bg-aqua/10 px-2 py-1 font-mono text-sm font-semibold text-aqua/95 sm:min-w-[14rem]">
                  {row.cmd}
                </code>
                <span className="text-sm leading-relaxed text-white/70">{row.body}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="mt-16">
          <h2 className="font-display text-2xl font-bold text-white sm:text-3xl">5 · Where to go next</h2>
          <div className="mt-8 grid gap-4 sm:grid-cols-2">
            {NEXT_LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="group flex flex-col gap-2 rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.04] to-transparent p-5 transition hover:border-aqua/40 hover:bg-white/[0.06]"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-display text-base font-bold text-white group-hover:text-aqua">{link.title}</span>
                  <span className="text-aqua/70 transition group-hover:translate-x-0.5">→</span>
                </div>
                <p className="text-sm leading-relaxed text-white/60">{link.body}</p>
              </Link>
            ))}
          </div>
        </section>

        <p className="mt-16 text-center text-sm text-white/40">
          Source for this page lives in{" "}
          <code className="font-mono text-aqua/80">landing/src/app/getting-started/page.tsx</code>. The wizard is{" "}
          <code className="font-mono text-aqua/80">landing/src/components/agent-setup-form.tsx</code>.
        </p>
      </main>
      <SiteFooter />
    </div>
  );
}

function PathCard({
  tag,
  title,
  blurb,
  command,
  footer,
}: {
  tag: string;
  title: string;
  blurb: string;
  command: string;
  footer: React.ReactNode;
}) {
  return (
    <article className="flex h-full flex-col rounded-2xl border border-white/10 bg-white/[0.03] p-6">
      <div className="flex items-center gap-2">
        <code className="rounded-full border border-aqua/30 bg-aqua/[0.08] px-2.5 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wide text-aqua/95">
          {tag}
        </code>
        <h3 className="font-display text-xl font-bold text-white">{title}</h3>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-white/70">{blurb}</p>
      <pre className="mt-4 overflow-x-auto rounded-xl border border-white/10 bg-black/55 p-3.5 font-mono text-[12px] leading-relaxed text-aqua/90">
{command}
      </pre>
      <p className="mt-4 text-xs leading-relaxed text-white/55">{footer}</p>
    </article>
  );
}
