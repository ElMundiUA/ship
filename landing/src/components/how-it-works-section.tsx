import Link from "next/link";
import { repoUrl } from "@/lib/config";

const steps = [
  {
    n: "01",
    title: "Init",
    body: "shipctl init writes .ship/config.yml (RFC-0002), resolves the starter artifacts for your preset, and installs per-agent rule files at the targets each collection declares. Nothing is copied blindly — every artifact is pinned by version.",
    code: "shipctl init --yes \\\n  --agents cursor,codex,claude-md \\\n  --tracker linear --ci gh-actions \\\n  --preset web-app",
  },
  {
    n: "02",
    title: "Sync",
    body: "shipctl fetches versioned artifacts from ship.elmundi.com and caches them under .ship/cache/. artifacts.pins freeze what your agents see; shipctl sync refreshes the cache without rewriting the pins.",
    code: "shipctl sync\nshipctl pattern list\nshipctl collection show preset-mobile-app",
  },
  {
    n: "03",
    title: "Verify",
    body: "Agents consume artifacts from the local cache and record the exact <kind>:<id>@<version> triples in each PR (RFC-0001). shipctl verify runs the bundled check registry — config schema, gitignore, rule markers, cache integrity, declared-agent disk signals.",
    code: "shipctl verify             # local + network\nshipctl verify --no-network  # CI smoke test",
  },
];

export function HowItWorksSection() {
  return (
    <section id="how-it-works" className="border-y border-white/10 bg-black/25 py-20 sm:py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">
          How it works · artifacts protocol v1
        </p>
        <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
          One protocol for every agent you run
        </h2>
        <p className="mt-4 max-w-3xl text-lg text-white/65">
          Ship serves versioned artifacts — patterns, tools, workflows, collections — from the same site you are reading.
          <code className="mx-1 rounded bg-white/10 px-1.5 py-0.5 font-mono text-[0.92em] text-aqua">shipctl</code> caches
          them locally under <code className="mx-1 rounded bg-white/10 px-1.5 py-0.5 font-mono text-[0.92em] text-aqua">.ship/cache/</code>,
          so agents run offline-first and record the exact versions they consumed in each pull request. Telemetry is opt-in.
        </p>

        <ol className="mt-12 grid gap-5 md:grid-cols-3">
          {steps.map((s) => (
            <li
              key={s.n}
              className="group relative flex flex-col rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.06] to-transparent p-6 shadow-card transition hover:border-aqua/35"
            >
              <div className="flex items-center gap-3">
                <span className="font-display text-2xl font-bold text-aqua/90">{s.n}</span>
                <span className="h-px flex-1 bg-gradient-to-r from-aqua/30 to-transparent" aria-hidden />
                <span className="rounded-full border border-white/10 bg-black/30 px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-white/55">
                  step
                </span>
              </div>
              <h3 className="font-display mt-3 text-xl font-bold text-white">{s.title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-white/65">{s.body}</p>
              <pre className="mt-4 overflow-x-auto rounded-lg border border-white/10 bg-black/50 p-3 font-mono text-[12px] leading-relaxed text-aqua/90">
{s.code}
              </pre>
            </li>
          ))}
        </ol>

        <div className="mt-10 flex flex-wrap items-center gap-3 text-sm">
          <Link
            className="inline-flex items-center rounded-full border border-aqua/30 bg-aqua/[0.08] px-4 py-1.5 font-semibold text-aqua hover:border-aqua/60"
            href="/docs/protocol/rfc-0001-artifacts-protocol"
          >
            Read RFC-0001 (artifacts protocol)
          </Link>
          <Link
            className="inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-4 py-1.5 font-semibold text-white/80 hover:border-white/30"
            href="/docs/protocol/rfc-0002-shipctl-config"
          >
            RFC-0002 (shipctl config)
          </Link>
          <Link
            className="inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-4 py-1.5 font-semibold text-white/80 hover:border-white/30"
            href="/docs/protocol/rfc-0003-telemetry-and-feedback"
          >
            RFC-0003 (telemetry opt-in)
          </Link>
          <a
            className="inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-4 py-1.5 font-semibold text-white/80 hover:border-white/30"
            href={`${repoUrl}/tree/main/documentation/rfc`}
            target="_blank"
            rel="noreferrer"
          >
            RFC index on GitHub
          </a>
        </div>
      </div>
    </section>
  );
}
