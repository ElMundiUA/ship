/**
 * Role-scope options surfaced in the Console policy editor.
 *
 * Slugs match ``spec.role`` in the artifact frontmatter (cloud-agent
 * roles) plus the synthetic ``"navigator"`` slug for the in-product
 * chat surface. The list is hand-curated rather than fetched from
 * the catalog so the form keeps working when the backend is
 * unreachable; new specialist roles need both an artifact and an
 * entry here.
 *
 * Order is grouped: chat first, then the SDLC pipeline (intake →
 * spec → impl → review), then audit / single-domain reviewers.
 */
export const ROLE_SCOPE_OPTIONS: ReadonlyArray<{
  slug: string;
  label: string;
}> = [
  { slug: "navigator", label: "Navigator (chat)" },
  { slug: "intake", label: "Intake" },
  { slug: "ba", label: "Business analyst" },
  { slug: "product-manager", label: "Product manager" },
  { slug: "developer", label: "Developer" },
  { slug: "clarification", label: "Clarification" },
  { slug: "qa-architect", label: "QA architect" },
  { slug: "tech-architect", label: "Tech architect" },
  { slug: "security-officer", label: "Security officer" },
  { slug: "designer", label: "Designer" },
  { slug: "mobile-reviewer", label: "Mobile reviewer" },
  { slug: "desktop-reviewer", label: "Desktop reviewer" },
  { slug: "ml-reviewer", label: "ML reviewer" },
  { slug: "game-balance-reviewer", label: "Game balance reviewer" },
];

export function roleLabel(slug: string): string {
  const match = ROLE_SCOPE_OPTIONS.find((o) => o.slug === slug);
  return match?.label ?? slug;
}
