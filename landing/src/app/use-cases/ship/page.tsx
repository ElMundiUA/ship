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
  title: "Use case — Ship (open methodology kit) — Ship",
  description:
    "How Ship packages instruction-first docs, patterns, tools, workflows, collections, and an agent API as one Apache-2.0 surface.",
};

const EVIDENCE = [
  {
    src: "/use-cases/ship-home.png",
    alt: "Ship marketing home — hero and pillars",
    caption: "Marketing home — positions the kit as methodology + automation, not a black-box SaaS.",
  },
  {
    src: "/use-cases/ship-patterns.png",
    alt: "Patterns catalog — cards for SDLC patterns",
    caption: "Patterns catalog — each card links to the canonical markdown under documentation/patterns.",
  },
  {
    src: "/use-cases/ship-docs-getting-started.png",
    alt: "Getting started chapter in the docs reader",
    caption: "Getting started — same typography and chrome as the rest of the reader for low context switch.",
  },
];

export default function ShipUseCasePage() {
  return (
    <>
      <SiteHeader />
      <main>
        <UseCaseHero
          eyebrow="Use case · Product story"
          title="Ship — packaging an open methodology kit like enterprise software"
          subtitle="One Next.js surface: marketing truth, searchable patterns, integrations catalog, workflow/collection manifests, MkDocs-style reader, and a small FastAPI for agents — Apache-2.0 so legal does not block a pilot."
          executive="Ship exists so teams can adopt agentic SDLC without trading governance for speed. The kit is deliberately boring architecture (markdown in git, manifests for catalogs, Actions for automation) so procurement, security, and engineers read the same artifacts."
        />

        <UseCaseSection id="challenge" kicker="01 · Challenge" title="Methodology content drifts from what teams actually run">
          <p>
            Playbooks in PDFs rot the day after export. Internal wikis diverge from CI. Buyers see glossy landing pages but
            cannot diff the operating model. Ship&apos;s challenge was to ship <strong>instruction-first truth</strong>{" "}
            that stays aligned with automation because both live in the same repository and render through the same app.
          </p>
        </UseCaseSection>

        <UseCaseSection id="solution" kicker="02 · Solution" title="Single surface: teach, browse, integrate, automate">
          <p>
            The solution is a deliberately small product footprint: <strong>documentation</strong> as the source of truth,{" "}
            <strong>patterns</strong> as reusable plays, <strong>tools</strong> as integration-facing stubs,{" "}
            <strong>workflows</strong> and <strong>collections</strong> as JSON manifests over markdown folders, and an{" "}
            <strong>agent API</strong> for machine-readable contracts. ElMundi is the reference org story; Ship is the
            product story of the same kit.
          </p>
        </UseCaseSection>

        <UseCaseSection id="implementation" kicker="03 · Implementation" title="How the kit is structured">
          <ul>
            <li>
              <strong>Landing app</strong> — Next.js App Router, Tailwind, shared header/footer, MDX/Markdown ingestion for
              docs and catalogs.
            </li>
            <li>
              <strong>Patterns + tools</strong> — file-backed content; URLs are stable paths for linking from Linear/Jira
              tickets.
            </li>
            <li>
              <strong>Workflows & collections</strong> — manifests at repo root for discoverability without a CMS.
            </li>
            <li>
              <strong>Backend</strong> — minimal FastAPI surface for agent operations (health + task intake) documented under
              Tools → Backend API.
            </li>
          </ul>
        </UseCaseSection>

        <UseCaseSection id="outcomes" kicker="04 · Outcomes" title="What good looks like for adopters">
          <ul>
            <li>
              <strong>Faster procurement loops</strong> — Apache-2.0, public repo, screenshots that match production routes.
            </li>
            <li>
              <strong>Lower onboarding tax</strong> — `/docs/getting-started` mirrors how engineers already read internal
              runbooks.
            </li>
            <li>
              <strong>Composable adoption</strong> — take patterns only, or wire the full ElMundi reference stack.
            </li>
          </ul>
        </UseCaseSection>

        <UseCaseEvidenceGrid items={EVIDENCE} />

        <UseCaseCtaRow
          links={[
            { href: "/docs/getting-started", label: "Getting started", variant: "primary" },
            { href: "/patterns", label: "Patterns", variant: "secondary" },
            { href: "/tools", label: "Tools / integrations", variant: "secondary" },
            { href: "/use-cases/elmundi", label: "Reference org (ElMundi)", variant: "secondary" },
          ]}
        />
      </main>
      <SiteFooter />
    </>
  );
}
