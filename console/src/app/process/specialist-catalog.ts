export type SpecialistTemplate = {
  id: string;
  name: string;
  role: string;
  artifactId?: string;
  tags?: string[];
};

const STATIC_SPECIALIST_CATALOG: SpecialistTemplate[] = [
  {
    id: "intake",
    name: "Intake specialist",
    role: "Clarifies incoming work, checks minimum context, and routes tasks.",
  },
  {
    id: "business_analyst",
    name: "Business analyst",
    role: "Turns ambiguous requests into requirements and acceptance criteria.",
  },
  {
    id: "product_manager",
    name: "Product manager",
    role: "Clarifies scope, priority, tradeoffs, and launch criteria.",
  },
  {
    id: "designer",
    name: "Designer",
    role: "Reviews UX flows, product copy, accessibility intent, and design quality.",
  },
  {
    id: "technical_architect",
    name: "Technical architect",
    role: "Plans architecture, migration strategy, boundaries, and technical risks.",
  },
  {
    id: "developer",
    name: "Developer",
    role: "Implements code changes, tests, docs, and prepares PRs.",
  },
  {
    id: "code_reviewer",
    name: "Code reviewer",
    role: "Reviews PRs for correctness, maintainability, risks, and test coverage.",
  },
  {
    id: "qa_engineer",
    name: "QA engineer",
    role: "Validates acceptance criteria, edge cases, and user-facing quality.",
  },
  {
    id: "qa_automation",
    name: "QA automation",
    role: "Adds or maintains automated tests and regression coverage.",
  },
  {
    id: "devops_platform",
    name: "DevOps/platform",
    role: "Handles CI/CD, environments, deployment, infrastructure, and operations.",
  },
  {
    id: "security_engineer",
    name: "Security engineer",
    role: "Reviews auth, permissions, secrets, dependencies, and security policy.",
  },
  {
    id: "data_ml_engineer",
    name: "Data/ML engineer",
    role: "Handles data pipelines, evaluations, experiments, and ML release checks.",
  },
  {
    id: "support_success",
    name: "Support/success",
    role: "Turns customer reports into reproducible tasks and validates fixes.",
  },
  {
    id: "technical_writer",
    name: "Technical writer",
    role: "Writes release notes, user docs, internal docs, and runbooks.",
  },
  {
    id: "marketing_operator",
    name: "Marketing operator",
    role: "Handles content, site, campaign, and marketing workflow tasks.",
  },
];

const ARTIFACT_SPECIALIST_CATALOG: SpecialistTemplate[] = [
  {
    id: "intake",
    name: "Intake",
    role: "Role prompt for intake lane on the SDLC grid.",
    artifactId: "role-intake",
    tags: ["intake", "triage"],
  },
  {
    id: "business_analyst",
    name: "BA / specification",
    role: "Specification and handoff quality before implementation picks.",
    artifactId: "role-ba",
    tags: ["ba", "spec"],
  },
  {
    id: "product_manager",
    name: "Product manager triage",
    role: "Triages freshly opened tickets, sizes them, assigns priority, and routes to the right role.",
    artifactId: "role-product-manager",
    tags: ["product", "triage"],
  },
  {
    id: "designer",
    name: "Designer review",
    role: "Reviews UI and design-touching work against design system, component, responsive, and copy conventions.",
    artifactId: "role-designer",
    tags: ["design", "review"],
  },
  {
    id: "technical_architect",
    name: "Tech architect",
    role: "Reviews architecture, migration strategy, technical debt, and cross-boundary risk.",
    artifactId: "role-tech-architect",
    tags: ["architecture", "tech-debt"],
  },
  {
    id: "developer",
    name: "Developer",
    role: "Implementation role for branch contract, PR shape, tests, and delivery evidence.",
    artifactId: "role-developer",
    tags: ["implementation", "pr"],
  },
  {
    id: "qa_architect",
    name: "QA architect",
    role: "Defines test strategy, automation hooks, and delivery-quality evidence.",
    artifactId: "role-qa-architect",
    tags: ["test-strategy", "automation"],
  },
  {
    id: "security_engineer",
    name: "Security officer",
    role: "Routes and reviews security findings without stealing delivery throughput.",
    artifactId: "role-security-officer",
    tags: ["security", "findings"],
  },
  {
    id: "data_ml_engineer",
    name: "ML reviewer",
    role: "Reviews training, inference, and data-pipeline changes for ML-specific pitfalls.",
    artifactId: "role-ml-reviewer",
    tags: ["ml", "review"],
  },
  {
    id: "clarification",
    name: "Clarification",
    role: "Creates structured follow-ups when requirements are incomplete.",
    artifactId: "role-clarification",
    tags: ["clarification", "requirements"],
  },
  {
    id: "desktop_reviewer",
    name: "Desktop native reviewer",
    role: "Reviews desktop native-integration surfaces, OS permissions, lifecycle, and privilege boundaries.",
    artifactId: "role-desktop-reviewer",
    tags: ["desktop", "native", "review"],
  },
  {
    id: "mobile_reviewer",
    name: "Mobile reviewer",
    role: "Reviews native mobile code for lifecycle, main-thread, memory, and battery pitfalls.",
    artifactId: "role-mobile-reviewer",
    tags: ["mobile", "native", "review"],
  },
  {
    id: "game_balance_reviewer",
    name: "Game balance reviewer",
    role: "Reviews tuning and balance changes against design intent and evidence.",
    artifactId: "role-game-balance-reviewer",
    tags: ["game", "balance", "review"],
  },
];

export const BASE_SPECIALIST_CATALOG: SpecialistTemplate[] =
  mergeSpecialistCatalogs(STATIC_SPECIALIST_CATALOG, ARTIFACT_SPECIALIST_CATALOG);

function mergeSpecialistCatalogs(
  fallback: SpecialistTemplate[],
  artifactBacked: SpecialistTemplate[],
): SpecialistTemplate[] {
  const byId = new Map<string, SpecialistTemplate>();
  for (const specialist of fallback) {
    byId.set(specialist.id, specialist);
  }
  for (const specialist of artifactBacked) {
    byId.set(specialist.id, specialist);
  }
  return Array.from(byId.values());
}
