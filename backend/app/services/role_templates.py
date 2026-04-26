"""Ship-managed specialist role templates.

First-build implementation is read-only seed data. The storage boundary is a
service instead of public artifacts so workspace DB-backed overrides can replace
this module without changing the process editor contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoleTemplate:
    id: str
    name: str
    description: str
    prompt_template: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    default_agent_profile: str = "auto"
    version: str = "ship-default-v1"
    source: str = "ship_managed"
    default_phases: tuple[str, ...] = field(default_factory=tuple)


_DEFAULT_ROLE_TEMPLATES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        id="intake",
        name="Intake specialist",
        description="Clarifies incoming work and validates minimum context.",
        prompt_template="Clarify incoming work, identify missing context, and route it to the correct process state.",
        capabilities=("triage", "clarification", "routing"),
        default_phases=("intake",),
    ),
    RoleTemplate(
        id="business_analyst",
        name="Business analyst",
        description="Turns requests into requirements and acceptance criteria.",
        prompt_template="Transform the selected ticket into acceptance criteria, constraints, and handoff notes.",
        capabilities=("requirements", "acceptance_criteria", "stakeholder_questions"),
        default_phases=("analysis",),
    ),
    RoleTemplate(
        id="product_manager",
        name="Product manager",
        description="Clarifies scope, priority, tradeoffs, and launch criteria.",
        prompt_template="Assess product intent, priority, risks, launch criteria, and required human decisions.",
        capabilities=("scope", "prioritization", "launch_criteria"),
        default_phases=("intake", "approval"),
    ),
    RoleTemplate(
        id="designer",
        name="Designer",
        description="Reviews UX flows, product copy, accessibility intent, and design quality.",
        prompt_template="Review user experience, product copy, accessibility implications, and design consistency.",
        capabilities=("ux", "copy", "accessibility"),
        default_phases=("design", "review"),
    ),
    RoleTemplate(
        id="technical_architect",
        name="Technical architect",
        description="Plans architecture, migration strategy, and risk boundaries.",
        prompt_template="Evaluate architecture, constraints, migrations, dependencies, and technical risk boundaries.",
        capabilities=("architecture", "planning", "risk_review"),
        default_phases=("planning", "review"),
    ),
    RoleTemplate(
        id="developer",
        name="Developer",
        description="Implements code changes, tests, and PR updates.",
        prompt_template="Implement the selected ticket with tests, evidence, and a reviewable PR shape.",
        capabilities=("implementation", "tests", "pull_requests"),
        default_phases=("implementation",),
    ),
    RoleTemplate(
        id="code_reviewer",
        name="Code reviewer",
        description="Reviews PRs for correctness, maintainability, risks, and test coverage.",
        prompt_template="Review the proposed change for correctness, maintainability, risk, and missing tests.",
        capabilities=("review", "ci_triage", "risk_review"),
        default_phases=("review",),
    ),
    RoleTemplate(
        id="qa_engineer",
        name="QA engineer",
        description="Validates acceptance criteria and quality gates.",
        prompt_template="Validate acceptance criteria, edge cases, regressions, and release quality evidence.",
        capabilities=("qa", "test_plans", "regression"),
        default_phases=("qa",),
    ),
    RoleTemplate(
        id="security_engineer",
        name="Security engineer",
        description="Reviews auth, permissions, secrets, dependencies, and security policy.",
        prompt_template="Review security-sensitive changes, permissions, secrets, dependencies, and policy impact.",
        capabilities=("security", "dependency_review", "permissions"),
        default_phases=("security", "review"),
    ),
    RoleTemplate(
        id="data_ml_engineer",
        name="Data/ML engineer",
        description="Handles data pipelines, evaluations, experiments, and ML release checks.",
        prompt_template="Review data, ML, evaluation, experiment, and pipeline implications.",
        capabilities=("data", "ml", "evaluation"),
        default_phases=("implementation", "review"),
    ),
    RoleTemplate(
        id="support_success",
        name="Support/success",
        description="Turns customer reports into reproducible tasks and validates fixes.",
        prompt_template="Translate customer context into reproducible evidence and validate support-facing outcomes.",
        capabilities=("support", "reproduction", "customer_context"),
        default_phases=("intake", "validation"),
    ),
    RoleTemplate(
        id="technical_writer",
        name="Technical writer",
        description="Writes release notes, user docs, internal docs, and runbooks.",
        prompt_template="Create clear documentation, release notes, runbooks, and user-facing explanations.",
        capabilities=("docs", "release_notes", "runbooks"),
        default_phases=("documentation",),
    ),
    RoleTemplate(
        id="marketing_operator",
        name="Marketing operator",
        description="Handles content, site, campaign, and marketing workflow tasks.",
        prompt_template="Prepare marketing content, campaign updates, and launch communication tasks.",
        capabilities=("marketing", "content", "campaigns"),
        default_phases=("marketing",),
    ),
    RoleTemplate(
        id="devops_platform",
        name="DevOps/platform",
        description="Handles runtime, CI, deployment, and operational health.",
        prompt_template="Review CI, deployment, infrastructure, reliability, and operational signals.",
        capabilities=("ci", "deployment", "infrastructure"),
        default_phases=("operations", "review"),
    ),
)


def default_role_templates() -> tuple[RoleTemplate, ...]:
    return _DEFAULT_ROLE_TEMPLATES


def role_template_by_id(role_id: str) -> RoleTemplate | None:
    return next((role for role in _DEFAULT_ROLE_TEMPLATES if role.id == role_id), None)
