export type SpecialistTemplate = {
  id: string;
  name: string;
  role: string;
  version?: string;
  source?: "ship_managed" | "workspace_custom";
  tags?: string[];
};

const STATIC_SPECIALIST_CATALOG: SpecialistTemplate[] = [
  {
    id: "navigator",
    name: "Navigator",
    role: "In-product chat agent that plans work, runs Inbox / Plays actions, and surfaces analytics.",
  },
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

export const BASE_SPECIALIST_CATALOG: SpecialistTemplate[] =
  STATIC_SPECIALIST_CATALOG.map((specialist) => ({
    ...specialist,
    version: "ship-default-v1",
    source: "ship_managed",
  }));
