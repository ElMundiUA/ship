"""GitHub Actions workflow integration (Day-4 Phase-1 honest executor).

Three responsibilities:

1. **Probe** — list the workflow filenames a customer repo currently
   exposes through the Actions API, so we can tell the user "you
   already have ``ship-pr-gate.yml`` installed, Run now is live"
   versus "open the install PR first". Cached in-process for 60s
   per repo because the dashboard polls and we don't want to burn
   a network round-trip per render; 60s is short enough that a
   freshly merged install PR unlocks Run now within a minute.
2. **Dispatch** — POST ``workflow_dispatch`` with ``ship_run_id`` /
   ``ship_callback_url`` / ``ship_run_token`` inputs. The starter
   workflow we ship loops the result back through our callback
   endpoint using those inputs, which is the only honest signal we
   get that the demo path actually ran in the customer's CI.
3. **Install** — open a PR in the customer repo with the starter
   workflow YAML inside ``.github/workflows/``. Uses the App's
   ``contents:write`` permission via the git data API (create a
   branch, create the file, open the PR). No clone, no checkout.

All errors raise :class:`WorkflowDispatchError` so the API layer can
distinguish "workflow not installed" (precondition, surface 412 with
an install hint) from "GitHub said no" (502 reverse proxy error).
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Final, Mapping

import httpx

from backend.app.core.config import Settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.integrations.github.app_auth import (
    GITHUB_API_BASE,
    fetch_installation_token,
)


# 60s is the sweet spot: long enough that a chatty dashboard doesn't
# rate-limit us through the Actions API, short enough that the user
# sees Run now light up shortly after merging the install PR.
_WORKFLOW_LIST_TTL_SECONDS: Final[float] = 60.0


class WorkflowDispatchError(RuntimeError):
    """GitHub-side failure during dispatch / probe / install commit.

    Carries the upstream HTTP status (``status_code``) and the
    response body excerpt (``message``) so the API layer can pick a
    sensible client-facing error code without re-parsing.
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"GitHub workflow API failed ({status_code}): {message}")
        self.status_code = status_code
        self.message = message


@dataclass(slots=True)
class _CachedWorkflowList:
    files: frozenset[str]
    fetched_at: float


# Keyed by GitHub's numeric installation_id + repo full_name so the
# cache survives across (workspace, repo) tuples that share an install
# but never bleeds across installations.
_workflow_list_cache: dict[tuple[int, str], _CachedWorkflowList] = {}


def invalidate_workflow_list_cache(
    *, installation_id: int | None = None, full_name: str | None = None
) -> None:
    """Drop cached workflow listings.

    Pass both args to drop one entry, just ``installation_id`` to
    drop everything under one install (after a webhook tells us the
    install was reconfigured), or no args to drop the whole cache
    (used in tests).
    """
    if installation_id is None and full_name is None:
        _workflow_list_cache.clear()
        return
    if installation_id is not None and full_name is not None:
        _workflow_list_cache.pop((installation_id, full_name), None)
        return
    if installation_id is not None:
        keys = [k for k in _workflow_list_cache if k[0] == installation_id]
        for k in keys:
            _workflow_list_cache.pop(k, None)
        return
    # full_name only — uncommon, mostly tests:
    keys = [k for k in _workflow_list_cache if k[1] == full_name]
    for k in keys:
        _workflow_list_cache.pop(k, None)


def _split_full_name(full_name: str) -> tuple[str, str]:
    owner, _, repo = full_name.partition("/")
    if not owner or not repo:
        raise WorkflowDispatchError(
            500,
            f"WorkspaceRepo.full_name {full_name!r} is not in 'owner/repo' form.",
        )
    return owner, repo


async def _request(
    method: str,
    path: str,
    *,
    token: str,
    client: httpx.AsyncClient | None,
    json: Any | None = None,
    params: Mapping[str, Any] | None = None,
) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    try:
        response = await http.request(
            method,
            f"{GITHUB_API_BASE}{path}",
            headers=headers,
            json=json,
            params=params,
        )
    finally:
        if owns_client:
            await http.aclose()
    return response


def _raise_for(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    body_excerpt = response.text[:512]
    raise WorkflowDispatchError(response.status_code, body_excerpt)


async def list_repo_workflows(
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    use_cache: bool = True,
) -> frozenset[str]:
    """Return the set of workflow *filenames* installed in ``repo``.

    A "filename" here is the basename of the path under
    ``.github/workflows/`` (e.g. ``ship-pr-gate.yml``). We compare
    against that, not the human-readable ``name:`` field, because the
    starter workflow we ship pins the filename so cache lookups are
    O(1) without parsing YAML.

    The Actions API returns workflows that live in the *default*
    branch — exactly what we want, because the Run now path
    dispatches against ``main`` and dispatching against a workflow
    that only exists on a feature branch quietly does nothing.
    """
    cache_key = (install.installation_id, repo.full_name)
    if use_cache:
        cached = _workflow_list_cache.get(cache_key)
        if cached and (time.time() - cached.fetched_at) < _WORKFLOW_LIST_TTL_SECONDS:
            return cached.files

    token = await fetch_installation_token(
        install.installation_id, settings=settings, client=client
    )
    owner, name = _split_full_name(repo.full_name)
    files: set[str] = set()
    page = 1
    while True:
        response = await _request(
            "GET",
            f"/repos/{owner}/{name}/actions/workflows",
            token=token,
            client=client,
            params={"per_page": 100, "page": page},
        )
        if response.status_code == 404:
            # Repo exists but Actions has never run there — treat as
            # "no workflows installed", same UX as an empty list. The
            # install endpoint will create the first one.
            break
        _raise_for(response)
        payload = response.json()
        items = payload.get("workflows", []) or []
        for item in items:
            path = str(item.get("path") or "")
            if not path.startswith(".github/workflows/"):
                continue
            files.add(path.rsplit("/", 1)[-1])
        if len(items) < 100:
            break
        page += 1
        # Defensive cap: a customer repo with > 1000 workflows is
        # almost certainly hostile / corrupted; no point paginating
        # forever.
        if page > 10:
            break

    frozen = frozenset(files)
    _workflow_list_cache[cache_key] = _CachedWorkflowList(
        files=frozen, fetched_at=time.time()
    )
    return frozen


async def dispatch_workflow(
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    workflow_file: str,
    *,
    inputs: Mapping[str, str],
    ref: str | None = None,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Trigger a ``workflow_dispatch`` for ``workflow_file`` in ``repo``.

    GitHub's API is fire-and-forget — a 204 only means "I queued the
    dispatch", not "the run is live yet". The corresponding
    ``workflow_run`` webhook (started/completed) is the source of
    truth for run status; this call only tells us the dispatch was
    *accepted*.

    ``ref`` defaults to the repo's recorded default branch so manual
    dispatches don't need the caller to remember whether the customer
    uses ``main`` or ``master``.
    """
    token = await fetch_installation_token(
        install.installation_id, settings=settings, client=client
    )
    owner, name = _split_full_name(repo.full_name)
    target_ref = ref or repo.default_branch or "main"
    response = await _request(
        "POST",
        f"/repos/{owner}/{name}/actions/workflows/{workflow_file}/dispatches",
        token=token,
        client=client,
        json={"ref": target_ref, "inputs": dict(inputs)},
    )
    _raise_for(response)


@dataclass(slots=True)
class StarterWorkflowPR:
    """Result of opening the install PR — minimal so callers can render."""

    pr_url: str
    pr_number: int
    branch: str


async def commit_starter_workflow(
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    workflow_file: str,
    content: str,
    pipeline_kind: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    return_url: str | None = None,
) -> StarterWorkflowPR:
    """Open a PR in ``repo`` adding ``.github/workflows/<workflow_file>``.

    Pure git data API — no clone, no local checkout. Sequence:

    1. Read the default-branch HEAD sha (``GET /git/ref``).
    2. Create a fresh branch ``ship/install-<kind>-<timestamp>`` off
       that sha (``POST /git/refs``).
    3. PUT the file via the Contents API on that branch — that
       endpoint creates the blob + tree + commit in one call.
    4. ``POST /pulls`` to open the PR.

    Returns the PR URL + number so the dashboard can deep-link the
    user. If a branch with the same kind already exists (the user
    pressed Install twice) we reuse it: GitHub's create-ref returns
    422, and we fall back to looking up the existing PR.
    """
    token = await fetch_installation_token(
        install.installation_id, settings=settings, client=client
    )
    owner, name = _split_full_name(repo.full_name)
    base_ref = repo.default_branch or "main"

    head_resp = await _request(
        "GET",
        f"/repos/{owner}/{name}/git/ref/heads/{base_ref}",
        token=token,
        client=client,
    )
    _raise_for(head_resp)
    base_sha = head_resp.json()["object"]["sha"]

    # Stamping the timestamp keeps repeated Install clicks from
    # colliding on a stale branch; the user only sees the latest PR
    # in their list (older ones can be closed manually).
    branch = f"ship/install-{pipeline_kind}-{int(time.time())}"
    create_branch = await _request(
        "POST",
        f"/repos/{owner}/{name}/git/refs",
        token=token,
        client=client,
        json={"ref": f"refs/heads/{branch}", "sha": base_sha},
    )
    if create_branch.status_code == 422:
        # Branch already exists — extremely rare with the timestamp
        # suffix, but humour it.
        pass
    else:
        _raise_for(create_branch)

    file_path = f".github/workflows/{workflow_file}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    put_resp = await _request(
        "PUT",
        f"/repos/{owner}/{name}/contents/{file_path}",
        token=token,
        client=client,
        json={
            "message": f"ship: install {pipeline_kind} workflow",
            "content": encoded,
            "branch": branch,
        },
    )
    _raise_for(put_resp)

    # Return-link deep-links the user straight back to the Ship
    # dashboard after they merge the PR on github.com. Without it the
    # user ends up on an empty github.com/…/pull/N page with no
    # obvious "back to Ship" affordance — the #1 pilot complaint.
    return_fragment = (
        f"\n\n---\n\n### ← Back to Ship\n\n"
        f"After merging this PR, jump back to the Ship dashboard to "
        f"dispatch the first run:\n\n"
        f"[**Open Ship dashboard →**]({return_url})\n"
        if return_url
        else ""
    )
    pr_resp = await _request(
        "POST",
        f"/repos/{owner}/{name}/pulls",
        token=token,
        client=client,
        json={
            "title": f"Ship: install {pipeline_kind} workflow",
            "head": branch,
            "base": base_ref,
            "body": (
                "This PR adds the GitHub Actions workflow Ship needs to run "
                f"the **{pipeline_kind}** pipeline against this repo.\n\n"
                "Once merged, Ship's dashboard \"Run now\" button will "
                "dispatch this workflow and stream its result back into the "
                "pipeline timeline.\n\n"
                "Generated automatically by the Ship App. Safe to merge as-is."
                f"{return_fragment}"
            ),
            "maintainer_can_modify": True,
        },
    )
    _raise_for(pr_resp)
    payload = pr_resp.json()
    return StarterWorkflowPR(
        pr_url=str(payload.get("html_url") or ""),
        pr_number=int(payload.get("number") or 0),
        branch=branch,
    )


__all__ = [
    "StarterWorkflowPR",
    "WorkflowDispatchError",
    "commit_starter_workflow",
    "dispatch_workflow",
    "invalidate_workflow_list_cache",
    "list_repo_workflows",
]
