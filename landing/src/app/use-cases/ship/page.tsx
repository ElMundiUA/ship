import type { Metadata } from "next";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import {
  UseCaseCtaRow,
  UseCaseEvidenceGrid,
  UseCaseHero,
  UseCaseResults,
  UseCaseSection,
  UseCaseSnapshot,
  UseCaseStakeholders,
} from "@/components/use-case-enterprise";

export const metadata: Metadata = {
  title: "Customer story — Ship (open methodology kit) — Ship",
  description:
    "How Ship packages forty chapters of operating doctrine, a versioned artifact catalog, a CLI, and an agent API as one Apache-2.0 surface — so security review is a license check, not a vendor questionnaire.",
};

const EVIDENCE = [
  {
    src: "/use-cases/ship-home.png",
    alt: "Ship marketing home — hero and pillars",
    caption:
      "Marketing home — positions the kit as methodology + automation, not a black-box SaaS. Same chrome as the docs reader so prospects do not feel a hand-off.",
  },
  {
    src: "/use-cases/ship-patterns.png",
    alt: "Patterns catalog — cards for SDLC patterns",
    caption:
      "Patterns catalog — each card links to the canonical artifact. Versioned, searchable from the CLI, citable from a Linear ticket.",
  },
  {
    src: "/use-cases/ship-docs-getting-started.png",
    alt: "Getting started chapter in the docs reader",
    caption:
      "Getting started — the same paragraph an engineer reads is the paragraph procurement reads, is the paragraph an agent loads as context.",
  },
];

export default function ShipUseCasePage() {
  return (
    <>
      <SiteHeader />
      <main>
        <UseCaseHero
          eyebrow="Customer story · Developer tools · Open methodology"
          title="Ship shipped the methodology like enterprise software so procurement stops blocking pilots"
          subtitle="One Next.js surface holds forty chapters of operating doctrine, a versioned artifact catalog (patterns, tools, workflows, collections), an installable CLI, and a small agent API. All of it is Apache-2.0, in a public git repository, and rendered from the same source the team itself runs on."
          executive="Ship exists because the methodology behind agentic SDLC was being lost in the gap between vendor decks and engineering wikis. By packaging the doctrine, the catalog, the tooling, and the API as one open-source surface, the kit removes the two biggest friction points for enterprise adoption: legal review and the &quot;does this match what you actually do?&quot; question. Pilots that used to take weeks of vendor meetings now start with a CLI command and a chapter."
        />

        <UseCaseSnapshot
          items={[
            { label: "Category", value: "SDLC methodology · open source" },
            { label: "License", value: "Apache-2.0" },
            { label: "Surface", value: "Docs · catalog · CLI · agent API" },
            { label: "Footprint", value: "Markdown + Next.js + FastAPI" },
          ]}
        />

        <UseCaseSection
          id="situation"
          kicker="01 · Situation"
          title="Methodology content drifts from what the team actually runs"
        >
          <p>
            Every engineering organisation accumulates a stack of operating documents — playbooks in PDFs, internal
            wikis, on-call runbooks, sales decks, vendor onboarding guides. The half-life of these documents in an
            agentic SDLC is measured in weeks. By the time the procurement form has been filled out, the playbook it
            references has changed, the workflow names in CI no longer match the diagrams, and the team is back to
            answering &quot;how do you actually do this?&quot; over Slack.
          </p>
          <p>
            That gap between <strong>how we say we work</strong> and <strong>how we actually work</strong> is the single
            largest tax on adopting any methodology — and the larger the buyer, the heavier the tax.
          </p>
        </UseCaseSection>

        <UseCaseSection
          id="complication"
          kicker="02 · Complication"
          title="Buyers cannot diff a vendor deck — and engineers will not read one"
        >
          <p>
            Two audiences had to be served with the same artifact: a non-engineering buying committee that needs to be
            convinced the methodology is real, and an engineering team that will reject anything that looks more like a
            sales deck than a runbook. The traditional answer — separate marketing site, separate docs site, separate
            git repo of templates — fails both: marketing rots, docs drift, and templates are version-locked the day
            they are copied.
          </p>
          <p>
            On top of the content problem, the legal one: enterprise teams cannot start a pilot of a closed-source
            methodology service without a vendor security review, an MSA, and a procurement cycle that routinely takes
            longer than the pilot itself.
          </p>
        </UseCaseSection>

        <UseCaseSection id="solution" kicker="03 · Resolution" title="A single, open surface — read by humans and agents">
          <p>
            Ship is one Next.js application backed by a small Python service. Marketing pages, the &quot;book&quot;
            (forty chapters of doctrine), the catalogs, and the CLI documentation all render from the same files
            engineers <em>and</em> agents read. The product surface is deliberately small:
          </p>
          <ul>
            <li>
              <strong>The book</strong> — long-form doctrine, downloadable as a print-quality PDF, also browsable
              chapter-by-chapter in the same chrome as the rest of the site.
            </li>
            <li>
              <strong>The catalog</strong> — versioned <em>patterns</em>, <em>tools</em>, <em>workflows</em>, and{" "}
              <em>collections</em> as folder-per-artifact units with structured frontmatter. Searchable from the CLI,
              citable from a ticket, diffable in git.
            </li>
            <li>
              <strong>shipctl</strong> — the installable command line that initialises a project, syncs the local
              catalog cache, verifies an installation, and pipes opt-in feedback back to the kit.
            </li>
            <li>
              <strong>Agent API</strong> — a small FastAPI surface that serves the same catalog data programmatically,
              so coding agents can pull patterns the same way humans browse them.
            </li>
            <li>
              <strong>License</strong> — Apache-2.0. No vendor questionnaire to fill in before a pilot.
            </li>
          </ul>
        </UseCaseSection>

        <UseCaseSection
          id="implementation"
          kicker="04 · Architecture"
          title="Boring stack on purpose, so adopters can audit and extend"
        >
          <p>
            The architecture is what enterprise security teams call &quot;refreshingly boring&quot;: markdown files in a
            git repository as the system of record; a Next.js application to render them; a small Python service to
            answer machine queries; GitHub Actions to keep the catalog in sync. There is no Ship-hosted control plane
            holding your code, your secrets, or your catalog. Every adopter runs their own instance against their own
            git remote.
          </p>
          <p>
            That choice was deliberate. It means a security team&apos;s threat model collapses to two questions:{" "}
            <em>do you trust the upstream Apache-2.0 repository?</em> and <em>do you trust your own GitHub org?</em> —
            both of which they have already answered.
          </p>
        </UseCaseSection>

        <UseCaseResults
          title="What the open packaging actually unlocks"
          caveat="Outcomes describe the effect of shipping the methodology this way, observed across early adopters of the kit. Numbers without a unit (&quot;Days, not weeks&quot;) are intentionally directional — the underlying repository, license, and CLI are public, so prospective adopters can verify the friction reduction first-hand."
          results={[
            {
              headline: "Days, not weeks",
              label: "Time-to-first-pilot",
              detail:
                "Apache-2.0 + public git removes the legal-review and procurement gates that typically front-load methodology rollouts. Engineering can run a real pilot before the MSA conversation even starts.",
            },
            {
              headline: "1 source",
              label: "Docs, catalog, CLI all read the same files",
              detail:
                "There is no separate &quot;sales-truth&quot; and &quot;engineering-truth&quot;. A buyer reading /docs and an agent loading a pattern over the API are reading the same paragraph.",
            },
            {
              headline: "Composable",
              label: "Take a single pattern or wire the whole stack",
              detail:
                "Adoption is incremental by design. Teams pull one pattern, validate it in their own context, then add the next. Nothing in the kit assumes you adopt all of it on day one.",
            },
          ]}
        />

        <UseCaseStakeholders
          items={[
            {
              role: "Engineering lead",
              perspective:
                "I can install shipctl, point it at our monorepo, and see exactly which patterns my team would inherit. No black box, no decoder ring — the artifacts are markdown files I can read in a terminal.",
            },
            {
              role: "Procurement / vendor management",
              perspective:
                "Apache-2.0, public repo, no SaaS dependency. The vendor risk review is a license check and a maintenance question, not a six-week security questionnaire.",
            },
            {
              role: "Security / CISO office",
              perspective:
                "Nothing leaves our perimeter unless we choose to send opt-in telemetry. The &quot;control plane&quot; is our own GitHub. The supply-chain question is the standard open-source one.",
            },
            {
              role: "Buying committee / GM",
              perspective:
                "The deck and the repository tell the same story. I can hand a chapter to an engineer and a chapter to legal and they read the same words. The pilot decision becomes a one-meeting conversation instead of a quarter-long evaluation.",
            },
          ]}
        />

        <UseCaseEvidenceGrid items={EVIDENCE} />

        <UseCaseCtaRow
          links={[
            { href: "/getting-started", label: "Run a pilot in your repo", variant: "primary" },
            { href: "/book", label: "Read the book", variant: "secondary" },
            { href: "/use-cases/elmundi", label: "Story 1 — ElMundi reference", variant: "secondary" },
            { href: "/use-cases", label: "All customer stories", variant: "secondary" },
          ]}
        />
      </main>
      <SiteFooter />
    </>
  );
}
