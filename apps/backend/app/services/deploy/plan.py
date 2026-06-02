"""``DeployPlan`` — the provider-agnostic deploy intermediate representation.

The LLM planner emits a ``DeployPlan``; each provider adapter
(DigitalOcean App Platform first) translates it **deterministically** into
that provider's spec. Keeping the IR provider-neutral is what lets us add
GitLab/Azure/other deploy targets later without re-running the model or
changing the planner.

Component ``kind`` deliberately uses DigitalOcean App Platform's component
vocabulary (``service`` / ``static_site`` / ``worker`` / ``job``) because
it is the broadest of the targets we care about and maps cleanly onto
others. ``service`` = an HTTP-exposed long-running process (this is what a
Streamlit/FastAPI/Express app is); ``static_site`` = pre-built assets on a
CDN; ``worker`` = a process with no inbound HTTP; ``job`` = a run-to-
completion task (migrations, seeds).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ComponentKind = Literal["service", "static_site", "worker", "job"]
Runtime = Literal["python", "node-js", "go", "ruby", "php", "static", "docker"]
Confidence = Literal["high", "medium", "low"]


class EnvVarSpec(BaseModel):
    """A single environment variable the component needs at runtime.

    We capture the *contract* (name, whether it's required/secret). For
    **secrets** the value is never set here — those come from the user /
    workspace secret store at execution time. For **non-secret config**
    the planner MAY set ``value`` when it can infer a correct deploy value
    — most importantly ``HOST=0.0.0.0`` so an env-configurable server binds
    to all interfaces in the container instead of localhost.
    """

    key: str = Field(description="Environment variable name, e.g. DATABASE_URL")
    required: bool = Field(
        default=True, description="Whether the app fails to boot without it"
    )
    secret: bool = Field(
        default=False,
        description="True if the value is sensitive (token, password, key)",
    )
    value: str | None = Field(
        default=None,
        description=(
            "Concrete value for NON-SECRET config the planner is confident "
            "about (e.g. HOST=0.0.0.0). Leave null for secrets and for "
            "values the operator must provide."
        ),
    )
    note: str | None = Field(
        default=None, description="Short hint about what it's for; NOT the value"
    )


class DeployComponent(BaseModel):
    """One deployable unit of the repo."""

    name: str = Field(
        description="Slug, 2-32 chars, lowercase letters/digits/hyphens"
    )
    kind: ComponentKind = Field(description="Component kind")
    runtime: Runtime = Field(
        description="Build/runtime family. Use 'docker' when a Dockerfile "
        "should drive the build, 'static' for prebuilt assets."
    )
    source_dir: str = Field(
        default="/",
        description="Path within the repo this component builds from "
        "(monorepo subdir). '/' for repo root.",
    )
    dockerfile_path: str | None = Field(
        default=None,
        description="Path to the Dockerfile when runtime='docker'.",
    )
    build_command: str | None = Field(
        default=None,
        description="Custom build step. Null to use the buildpack default.",
    )
    run_command: str | None = Field(
        default=None,
        description="Start command for service/worker. For Streamlit use "
        "'streamlit run app.py --server.port 8080 --server.address 0.0.0.0'. "
        "Null for static_site.",
    )
    output_dir: str | None = Field(
        default=None,
        description="For static_site: directory of built assets (e.g. 'dist', 'build', 'out').",
    )
    http_port: int | None = Field(
        default=None,
        description="Port the service listens on. Prefer 8080. Null for "
        "static_site/worker/job.",
    )
    routes: list[str] = Field(
        default_factory=lambda: ["/"],
        description="HTTP path prefixes routed to this component (service/static_site).",
    )
    health_check_path: str | None = Field(
        default=None,
        description="HTTP path that returns 2xx when healthy. Streamlit: "
        "'/_stcore/health'. Pick a real endpoint; null falls back to a TCP check.",
    )
    env: list[EnvVarSpec] = Field(
        default_factory=list, description="Environment variables this component needs"
    )
    instance_size: str = Field(
        default="basic-xxs",
        description="Provider instance size hint; 'basic-xxs' is the small default.",
    )


class DeployPlan(BaseModel):
    """The full plan for deploying a repository to a cloud provider."""

    summary: str = Field(
        description="One human sentence: what this is and how it deploys, "
        "e.g. 'Streamlit data app deployed as a single Python web service.'"
    )
    app_name: str = Field(
        description="Slug for the whole app, 2-32 chars, lowercase/hyphen."
    )
    components: list[DeployComponent] = Field(
        description="One or more deployable components."
    )
    needs_managed_db: str | None = Field(
        default=None,
        description="Managed database engine the app needs ('postgres', "
        "'mysql', 'redis'), or null if none detected.",
    )
    confidence: Confidence = Field(
        description="How confident the plan is given the available signals."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Caveats the operator should know (e.g. 'private repo "
        "requires authorizing DigitalOcean GitHub access', 'no health "
        "endpoint found', 'secrets must be supplied before first boot').",
    )

    @staticmethod
    def llm_json_schema() -> dict:
        """JSON schema for forcing structured model output (tool-use / JSON mode)."""
        return DeployPlan.model_json_schema()


__all__ = [
    "ComponentKind",
    "Confidence",
    "DeployComponent",
    "DeployPlan",
    "EnvVarSpec",
    "Runtime",
]
