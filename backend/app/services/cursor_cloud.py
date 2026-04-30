"""Cursor Cloud Agent API wrapper.

Single-shot dispatch: ``launch_agent(prompt, repo_url, branch_name, …)``
posts to ``https://api.cursor.com/v0/agents`` and returns the agent
metadata. Mirrors what
``tools/linear-agent/scripts/cloud-agent-launch.mjs`` does in the
ElMundi sibling repo.

Lives in ``services/`` (not ``integrations/``) because Cursor Cloud is
the **agent runtime** — the thing the routine handler hands the prompt
off to — not a tracker or a code host. There's no FSM or polling here.
The agent writes back to its own outputs (PRs, issue comments) using
its own tools; the Ship server just kicks the work off and returns.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx


logger = logging.getLogger(__name__)


CURSOR_API_BASE = "https://api.cursor.com"


class CursorCloudError(RuntimeError):
    """Raised when the Cursor Cloud API rejects a launch request.

    Carries the upstream HTTP status + body so the caller can choose
    between mapping to a specific user-facing error (4xx) or treating
    it as a transient infra fault (5xx).
    """

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"Cursor Cloud API returned {status}: {body!r}")
        self.status = status
        self.body = body


@dataclass(frozen=True)
class LaunchedAgent:
    """Agent metadata returned by the Cursor Cloud launch."""

    agent_id: str
    branch_name: str
    raw: dict[str, Any]


async def launch_agent(
    *,
    api_key: str,
    prompt: str,
    repo_url: str,
    branch_name: str,
    ref: str = "main",
    auto_create_pr: bool = False,
    open_as_cursor_github_app: bool = False,
    client: httpx.AsyncClient | None = None,
    timeout: float = 30.0,
) -> LaunchedAgent:
    """Kick off a single Cursor Cloud agent run.

    The agent checks out ``repo_url`` at ``ref``, creates ``branch_name``,
    and runs ``prompt``. If ``auto_create_pr`` is true the runtime opens a
    PR back to ``ref`` as soon as the branch has a commit; otherwise the
    prompt is responsible for opening the PR itself (developer roles in
    practice — they want to write the PR title/body deliberately).
    """
    if not api_key:
        raise CursorCloudError(0, "missing CURSOR_API_KEY")

    auth = "Basic " + base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    body = {
        "prompt": {"text": prompt},
        "source": {"repository": repo_url, "ref": ref},
        "target": {
            "branchName": branch_name,
            "autoCreatePr": auto_create_pr,
            "openAsCursorGithubApp": open_as_cursor_github_app,
        },
    }

    async def _post(c: httpx.AsyncClient) -> httpx.Response:
        return await c.post(
            f"{CURSOR_API_BASE}/v0/agents",
            json=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": auth,
            },
            timeout=timeout,
        )

    if client is None:
        async with httpx.AsyncClient() as fresh:
            res = await _post(fresh)
    else:
        res = await _post(client)

    text = res.text
    if not res.is_success:
        # Don't try to JSON-decode an error response — Cursor's 4xx/5xx
        # bodies are sometimes plain strings.
        raise CursorCloudError(res.status_code, text)

    try:
        data = res.json()
    except Exception as exc:  # pragma: no cover — non-JSON 200 is unexpected
        raise CursorCloudError(res.status_code, text) from exc

    agent_id = (
        data.get("id")
        or data.get("agentId")
        or data.get("agent_id")
        or ""
    )
    return LaunchedAgent(
        agent_id=str(agent_id),
        branch_name=branch_name,
        raw=data,
    )
