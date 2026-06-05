"""Agent API-key catalog and per-repo secret wiring.

The Wizard v2 step "pick AI agents" needs three things:

1. A **catalog** of supported agents with the GitHub Actions secret
   name each one reads. We don't try to cover every shell-fluent
   coding assistant — only the ones Ship's render/run pipelines
   actually orchestrate through ``run-agent.yml`` today. Everything
   else stays manual: the user configures it themselves, outside
   Ship, because we have nothing useful to say about its secret.

2. A **check** against the repo's existing secrets so the wizard
   can tell "you already have ``ANTHROPIC_API_KEY`` set, nothing
   to do" from "missing — please paste a value". Plaintext of
   already-configured secrets is never readable (GitHub's Actions
   API hides it), so "present" is the strongest signal we have.

3. An **upsert** path that takes plaintext from the wizard,
   pushes it via :func:`put_repo_secret`, and forgets it. The
   plaintext only exists in memory for the HTTP exchange with
   GitHub. We never persist it — not in ``repo_secrets`` (that's
   the customer-managed-secrets table and has different rotation
   semantics), not in ``integrations.secret_ciphertext``, not in
   any logs.

The catalog is intentionally small. **LLM vendor keys** (Anthropic /
Cursor / OpenAI) are optional in the wizard gate: the operator only
needs **one** of them for whichever agent they run (or none when using
GitHub Copilot, which uses ``GITHUB_TOKEN``). ``resolve_agent_secret_status``
marks those rows ``required=False`` so the seed PR is not blocked while
still surfacing GitHub ``present`` state. **Copilot** stays
``required_secret=None`` — always "ready" without prompting.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from backend.app.core.config import Settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.integrations.github.actions_secrets import (
    list_repo_secrets,
    put_repo_secret,
)


@dataclass(frozen=True)
class AgentSecretSpec:
    """Declarative description of what a given agent needs to run.

    ``slug`` matches the ids used in ``cli/lib/detect.mjs`` / v2
    config's ``stack.agents`` so the wizard doesn't have to maintain
    a second translation table.

    ``secret_name`` is the GitHub Actions secret the agent actually
    reads inside the runner. ``None`` means "no secret required"
    (either the agent piggy-backs on the GitHub-provided token, or
    it authenticates through something else Ship doesn't touch).

    ``vendor_url`` is a one-click link to where the user goes to
    mint a key — purely UX sugar so the wizard can show a "Get
    key" button next to the input field.
    """

    slug: str
    label: str
    secret_name: str | None
    vendor_url: str | None = None
    description: str | None = None


# Order matters: the wizard renders the catalog in this order, and
# we want the "most likely picked" agents at the top of the list so
# the majority onboarding flow is a two-click affair.
AGENT_SECRET_CATALOG: tuple[AgentSecretSpec, ...] = (
    AgentSecretSpec(
        slug="claude-md",
        label="Claude Code (Anthropic)",
        secret_name="ANTHROPIC_API_KEY",
        vendor_url="https://console.anthropic.com/settings/keys",
        description=(
            "Claude Code uses this key to authenticate every agent "
            "invocation. Any plan with API access works."
        ),
    ),
    AgentSecretSpec(
        slug="cursor-cloud",
        label="Cursor Cloud Agent",
        secret_name="CURSOR_API_KEY",
        vendor_url="https://cursor.com/dashboard",
        description=(
            "Cursor's cloud agent authenticates through a Cursor "
            "API key scoped to your team."
        ),
    ),
    AgentSecretSpec(
        slug="codex",
        label="OpenAI Codex (codex CLI)",
        secret_name="OPENAI_API_KEY",
        vendor_url="https://platform.openai.com/api-keys",
        description=(
            "Any org-scoped key with chat/completions access works."
        ),
    ),
    AgentSecretSpec(
        slug="llm-openai",
        label="LLM API key (OpenAI)",
        secret_name="OPENAI_API_KEY",
        vendor_url="https://platform.openai.com/api-keys",
        description="Optional. Usable by deployment planning and future AI workflows.",
    ),
    AgentSecretSpec(
        slug="llm-anthropic",
        label="LLM API key (Anthropic)",
        secret_name="ANTHROPIC_API_KEY",
        vendor_url="https://console.anthropic.com/settings/keys",
        description="Optional. Usable by deployment planning and future AI workflows.",
    ),
    AgentSecretSpec(
        slug="llm-gemini",
        label="LLM API key (Gemini)",
        secret_name="GEMINI_API_KEY",
        vendor_url="https://aistudio.google.com/app/apikey",
        description=(
            "Optional. Usable by deployment planning and future AI workflows."
        ),
    ),
    AgentSecretSpec(
        slug="llm-mistral",
        label="LLM API key (Mistral)",
        secret_name="MISTRAL_API_KEY",
        vendor_url="https://console.mistral.ai/api-keys",
        description=(
            "Optional. Usable by deployment planning and future AI workflows."
        ),
    ),
    AgentSecretSpec(
        slug="copilot",
        label="GitHub Copilot",
        secret_name=None,
        vendor_url=None,
        description=(
            "Copilot authenticates off the runner's GITHUB_TOKEN — "
            "nothing to configure here."
        ),
    ),
)


# Lookup by slug for O(1) access from the check-or-push endpoint.
_CATALOG_BY_SLUG: dict[str, AgentSecretSpec] = {
    spec.slug: spec for spec in AGENT_SECRET_CATALOG
}

# Slugs whose GitHub secrets are mutually substitutable for the wizard:
# pick **one** vendor key for the agent you use (or skip all if Copilot).
_LLM_VENDOR_SLUGS: frozenset[str] = frozenset(
    {
        "claude-md",
        "cursor-cloud",
        "codex",
        "llm-openai",
        "llm-anthropic",
        "llm-gemini",
        "llm-mistral",
    }
)


def lookup_agent(slug: str) -> AgentSecretSpec | None:
    """Return the catalog entry for ``slug`` or ``None`` if unknown."""

    return _CATALOG_BY_SLUG.get(slug)


@dataclass(frozen=True)
class AgentSecretStatus:
    """Current wiring state for one agent on one repo.

    ``present`` is ``True`` when the GitHub API reports a secret with
    the right name already set on the repo — which is the only
    signal we have (GitHub doesn't expose the plaintext to compare
    against). ``False`` means the wizard should prompt the user.

    Agents whose ``spec.secret_name is None`` always come back with
    ``present=True`` and ``required=False`` — the wizard can render
    them as "ready" without prompting.
    """

    slug: str
    label: str
    required: bool
    secret_name: str | None
    present: bool
    vendor_url: str | None
    description: str | None


async def resolve_agent_secret_status(
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    slugs: list[str],
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> list[AgentSecretStatus]:
    """Return "what do we still need?" for each requested agent slug.

    Silently skips slugs that aren't in the catalog — the wizard
    enforces valid ids on its side; returning a 422 here would
    couple the wizard's agent list and the backend's catalog too
    tightly for what is essentially a UI affordance.

    One ``list_repo_secrets`` round-trip covers all slugs because
    we intersect locally — avoids N API calls for N agents.
    """

    specs: list[AgentSecretSpec] = []
    for slug in slugs:
        spec = lookup_agent(slug)
        if spec is not None:
            specs.append(spec)
    if not specs:
        return []

    # Only go to GitHub if at least one picked agent actually needs
    # a secret. The common copilot-only case avoids a round-trip.
    needed_any = any(spec.secret_name for spec in specs)
    present_names: set[str] = set()
    if needed_any:
        present_names = set(
            await list_repo_secrets(
                repo, install, settings=settings, client=client
            )
        )

    return [
        AgentSecretStatus(
            slug=spec.slug,
            label=spec.label,
            required=(
                spec.secret_name is not None
                and spec.slug not in _LLM_VENDOR_SLUGS
            ),
            secret_name=spec.secret_name,
            present=(
                spec.secret_name is None
                or spec.secret_name in present_names
            ),
            vendor_url=spec.vendor_url,
            description=spec.description,
        )
        for spec in specs
    ]


async def push_agent_secret(
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    slug: str,
    plaintext: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Push the user-supplied ``plaintext`` as ``spec.secret_name``.

    Returns the GitHub ``key_id`` used to seal the value, so the
    caller can log a "sealed under key K" audit trail without ever
    seeing the plaintext itself. Raises for unknown slugs or for
    agents that don't require a secret — the UI shouldn't let those
    through, and swallowing silently would hide bugs.

    Plaintext is handed straight to :func:`put_repo_secret` which
    sealed-box-encrypts it and PUTs to GitHub. Nothing in Ship's
    storage layer touches it; the argument's reference dies when
    this function returns.
    """

    spec = lookup_agent(slug)
    if spec is None:
        raise ValueError(f"unknown agent slug: {slug!r}")
    if spec.secret_name is None:
        raise ValueError(
            f"agent {slug!r} does not require a secret; nothing to push"
        )
    if not isinstance(plaintext, str) or not plaintext.strip():
        raise ValueError("plaintext secret must be a non-empty string")

    return await put_repo_secret(
        repo,
        install,
        name=spec.secret_name,
        plaintext=plaintext,
        settings=settings,
        client=client,
    )


__all__ = [
    "AGENT_SECRET_CATALOG",
    "AgentSecretSpec",
    "AgentSecretStatus",
    "lookup_agent",
    "push_agent_secret",
    "resolve_agent_secret_status",
]
