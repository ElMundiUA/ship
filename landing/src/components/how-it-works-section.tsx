import Link from "next/link";
import { repoUrl } from "@/lib/config";

const steps = [
  {
    n: "01",
    title: "Bootstrap",
    body: "shipctl init writes .ship/config.yml (RFC-0002), resolves the starter Plays for your stack, and installs per-agent rule files at the targets each collection declares. Nothing is copied blindly — every artifact is pinned by version.",
    code: "shipctl init --yes \\\n  --agents cursor,codex,claude-md \\\n  --tracker linear --ci gh-actions",
  },
  {
    n: "02",
    title: "Pick Plays",
    body: "Browse the same catalog the site publishes. A Play is a versioned operational procedure (PR self-review, release cut, knowledge refresh) backed by a pattern artifact. shipctl sync caches them under .ship/cache/ so agents run offline-first.",
    code: "shipctl sync --lock\nshipctl pattern list\nshipctl pattern show flow-pr-self-review",
  },
  {
    n: "03",
    title: "Assign as Automations",
    body: "Bind a Play to a scope and a trigger and you have an Automation — declared in lanes:, generated as a thin GitHub Actions wrapper around run-agent.yml. Same lane definition shows up in the operator console as an Automation row.",
    code: "shipctl lanes install --yes\n# or, operator-friendly alias:\nshipctl automations install --yes",
  },
  {
    n: "04",
    title: "Watch Runs",
    body: "Every dispatch produces a Run with an outcome-first row in the console: \"Reviewed PR · 3 suggestions · 1 fix applied\". The pattern reports its own RunSummary via shipctl callback so the row writes itself.",
    code: "shipctl callback --status ok \\\n  --outcome-text \"Reviewed PR · 3 suggestions\" \\\n  --findings-count 3",
  },
  {
    n: "05",
    title: "Triage Inbox",
    body: "When a Run needs a human — clarification, approval, escalation — it lands in the Inbox routed to the right owner via CODEOWNERS. One disposition closes the loop: accept, reject, snooze, or reassign.",
    code: "# the Navigator agent can do this for you:\n\"What's in my inbox?\"\n\"Resolve the PR-review item with a fix-it-myself note.\"",
  },
];

export function HowItWorksSection() {
  return (
    <section id="how-it-works" className="border-y border-white/10 bg-black/25 py-20 sm:py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">
          How it works · five steps from repo to operator loop
        </p>
        <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
          One protocol for every agent. One console for every operator.
        </h2>
        <p className="mt-4 max-w-3xl text-lg text-white/65">
          Ship serves versioned artifacts — patterns (Plays), tools, collections — from the same site you are reading.{" "}
          <code className="mx-1 rounded bg-white/10 px-1.5 py-0.5 font-mono text-[0.92em] text-aqua">shipctl</code> caches
          them locally under{" "}
          <code className="mx-1 rounded bg-white/10 px-1.5 py-0.5 font-mono text-[0.92em] text-aqua">.ship/cache/</code>,
          so agents run offline-first and record the exact versions they consumed in each pull request. Every Run
          reports back via{" "}
          <code className="mx-1 rounded bg-white/10 px-1.5 py-0.5 font-mono text-[0.92em] text-aqua">shipctl callback</code>{" "}
          so the operator console can render an outcome row and route any escalations into the Inbox. Telemetry is opt-in.
        </p>

        <ol className="mt-12 grid gap-5 md:grid-cols-2 lg:grid-cols-5">
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
            href="/docs/concepts"
          >
            Concepts (Plays / Automations / Runs / Inbox)
          </Link>
          <Link
            className="inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-4 py-1.5 font-semibold text-white/80 hover:border-white/30"
            href="/docs/protocol/rfc-0010-plays-and-inbox"
          >
            RFC-0010 (operator IA)
          </Link>
          <Link
            className="inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-4 py-1.5 font-semibold text-white/80 hover:border-white/30"
            href="/docs/protocol/rfc-0001-artifacts-protocol"
          >
            RFC-0001 (artifacts protocol)
          </Link>
          <Link
            className="inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-4 py-1.5 font-semibold text-white/80 hover:border-white/30"
            href="/docs/protocol/rfc-0002-shipctl-config"
          >
            RFC-0002 (shipctl config)
          </Link>
          <a
            className="inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-4 py-1.5 font-semibold text-white/80 hover:border-white/30"
            href={`${repoUrl}/tree/main/documentation/protocol`}
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
