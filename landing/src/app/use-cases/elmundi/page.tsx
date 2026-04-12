import type { Metadata } from "next";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import {
  UseCaseCtaRow,
  UseCaseEvidenceGrid,
  UseCaseHero,
  UseCaseSection,
} from "@/components/use-case-enterprise";

export const metadata: Metadata = {
  title: "Use case — ElMundi (reference org) — Ship",
  description:
    "How a public monorepo wires Linear, GitHub Actions, Cursor Cloud Agent, SDLC audits, and Playwright — with receipts in-repo.",
};

const EVIDENCE = [
  {
    src: "/use-cases/elmundi-dev-home.png",
    alt: "ElMundi consumer app home on dev.elmundi.com",
    caption: "Product — dev.elmundi.com home (staging): the customer-facing app, not the Ship manual.",
  },
  {
    src: "/use-cases/elmundi-dev-collections.png",
    alt: "ElMundi collections on dev.elmundi.com",
    caption: "Product — collections on staging for how the live experience reads under real traffic.",
  },
  {
    src: "/use-cases/elmundi-manual-chapter.png",
    alt: "Ship manual chapter for ElMundi reference wiring",
    caption: "Operator depth — same story in the Ship reader (/docs/examples/elmundi) for filenames, gates, and cron tables.",
  },
];

export default function ElmundiUseCasePage() {
  return (
    <>
      <SiteHeader />
      <main>
        <UseCaseHero
          eyebrow="Use case · Reference org"
          title="ElMundi — operating a public monorepo like an enterprise program"
          subtitle="Linear as the SDLC spine, GitHub Actions as the nervous system, Cursor Cloud Agent for execution, Playwright for proof — all documented in-repo so procurement can read the same story engineers diff."
          executive="ElMundi is the reference wiring for Ship: not a separate product, but a living receipt of how the methodology kit is applied to a real org surface. The outcome is predictable delivery with audit trails (labels, states, Actions logs) instead of heroics in chat threads."
        />

        <UseCaseSection id="challenge" kicker="01 · Challenge" title="Scale agent execution without losing governance">
          <p>
            Teams want autonomous agents, but enterprise risk asks for <strong>who approved what</strong>,{" "}
            <strong>which branch ran</strong>, and <strong>how QA was evidenced</strong>. ElMundi encodes those answers as
            infrastructure: Linear projects mirror SDLC lanes, GitHub Actions own scheduling and secrets, and the manual
            names the exact files so security can grep instead of guessing.
          </p>
        </UseCaseSection>

        <UseCaseSection id="solution" kicker="02 · Solution" title="Treat the monorepo as the control plane">
          <p>
            The fix is not another dashboard — it is <strong>instruction-first documentation</strong> plus{" "}
            <strong>automation that matches the words</strong>. Ship ships the patterns; ElMundi shows one complete install:
            intake → build → QA → release, with receipts in <code className="text-aqua">.github/workflows</code>,{" "}
            <code className="text-aqua">documentation/examples/elmundi</code>, and the Linear taxonomy described in the
            manual.
          </p>
        </UseCaseSection>

        <UseCaseSection id="implementation" kicker="03 · Implementation" title="What was actually wired">
          <ul>
            <li>
              <strong>Linear</strong> — SDLC projects, states, labels, and audit issues that agents must satisfy before
              merge.
            </li>
            <li>
              <strong>GitHub Actions</strong> — scheduled and on-demand workflows for agent launch, docs sync, and release
              hygiene; secrets stay in GitHub, not in prompts.
            </li>
            <li>
              <strong>Cursor Cloud Agent</strong> — branch-per-ticket execution with PRs as the review surface.
            </li>
            <li>
              <strong>Playwright</strong> — hosted browser runs that attach traces/screenshots to the ticket narrative.
            </li>
            <li>
              <strong>MkDocs-style reader</strong> — the same content is readable on the marketing site under{" "}
              <code className="text-aqua">/docs/examples/elmundi</code> for buyers who will not clone first.
            </li>
          </ul>
        </UseCaseSection>

        <UseCaseSection id="outcomes" kicker="04 · Outcomes" title="What leadership can point to">
          <ul>
            <li>
              <strong>Repeatable agent launches</strong> — operators follow named workflows instead of improvising slash
              commands.
            </li>
            <li>
              <strong>Audit-friendly artifacts</strong> — Actions logs, PR links, and Linear state transitions form a chain
              of custody.
            </li>
            <li>
              <strong>Faster onboarding</strong> — new contributors read one manual chapter and see the same filenames in
              the tree.
            </li>
          </ul>
        </UseCaseSection>

        <UseCaseEvidenceGrid
          items={EVIDENCE}
          evidenceIntro="Product screenshots are from dev.elmundi.com staging; the last tile is the Ship manual chapter for operators who need diffable wiring detail."
        />

        <UseCaseCtaRow
          links={[
            { href: "/docs/examples/elmundi", label: "Technical manual (deep dive)", variant: "primary" },
            { href: "/workflows", label: "Browse workflows", variant: "secondary" },
            { href: "/collections", label: "Collections", variant: "secondary" },
            { href: "/use-cases", label: "All use cases", variant: "secondary" },
          ]}
        />
      </main>
      <SiteFooter />
    </>
  );
}
