"""GitHub Actions repo-secrets API client (B10).

Three operations wrap the Actions-secrets REST surface:

- :func:`get_repo_public_key` — fetch the repo's sealed-box public key
  (``GET /repos/{owner}/{repo}/actions/secrets/public-key``). GitHub
  mints one per repo and rotates it when the repo is transferred or
  re-created; we re-fetch on every write because caching the key and
  encrypting against a stale one results in a 422 from the upstream
  ``PUT`` and a dangling UI row.
- :func:`put_repo_secret` — sealed-box encrypt a plaintext against
  that key (``nacl.public.SealedBox``) and upload via
  ``PUT /repos/{owner}/{repo}/actions/secrets/{name}``. GitHub
  returns 201 on create, 204 on update — we treat both as success.
- :func:`delete_repo_secret` — ``DELETE /repos/.../actions/secrets/{name}``,
  treating 404 as "already gone" so the UI can render "deleted"
  idempotently.

All three use the App installation token (not the App JWT) because
Actions secrets are a repo-scoped permission and the installation is
the only credential holding ``actions:write``. Token minting reuses
:func:`fetch_installation_token`; the HTTP layer mirrors
:mod:`backend.app.integrations.github.workflows` so retries /
timeouts / error envelopes are consistent across GitHub clients.

Encryption detail worth a comment
---------------------------------

GitHub's Actions Secrets API accepts **only** libsodium sealed-box
ciphertext, base64-encoded, with ``key_id`` naming the public key
the ciphertext was sealed to. PyNaCl's :class:`SealedBox` wraps the
X25519+XSalsa20+Poly1305 construction GitHub documents; the
ephemeral-key handshake means we don't need to ship our own key
material — we just encrypt and throw the ephemeral key away. No
other encryption format works.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Final

import httpx
from nacl import encoding, public

from backend.app.core.config import Settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.integrations.github.app_auth import fetch_installation_token
from backend.app.integrations.github.workflows import (
    WorkflowDispatchError,
    _raise_for,
    _request,
    _split_full_name,
)


# Actions secret-name rules (lifted straight from GitHub's docs):
# uppercase letters, digits, underscores, must not start with a
# digit, must not start with ``GITHUB_``. Length 1..245. The name
# validation happens in the API route so the error message is
# user-facing ("use A-Z, 0-9, _") rather than a 422 from github.com.
_MAX_SECRET_NAME: Final[int] = 245


@dataclass(slots=True)
class RepoPublicKey:
    """The repo's sealed-box key as GitHub returns it."""

    key_id: str
    key_b64: str  # base64-encoded 32-byte Curve25519 public key

    def encrypt(self, plaintext: str) -> str:
        """Return the base64 ciphertext to drop into the PUT body.

        Creates a fresh ephemeral SealedBox for each call because
        :class:`nacl.public.SealedBox` stores no per-instance state
        and the underlying libsodium API already generates a fresh
        ephemeral keypair per ``.encrypt()``. Pure convenience.
        """
        if not isinstance(plaintext, str):
            raise TypeError("encrypt() expects a string plaintext")
        public_key = public.PublicKey(
            self.key_b64.encode("utf-8"), encoder=encoding.Base64Encoder
        )
        sealed = public.SealedBox(public_key).encrypt(
            plaintext.encode("utf-8")
        )
        return base64.b64encode(sealed).decode("utf-8")


async def get_repo_public_key(
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> RepoPublicKey:
    """Fetch the sealed-box public key for ``repo``.

    404 is a real precondition error — the Installation can't see
    Actions on the repo (Actions disabled org-wide, or repo was
    archived). We surface it as :class:`WorkflowDispatchError` so
    the API layer can translate to a user-facing 412 with "Enable
    Actions on this repo first".
    """
    token = await fetch_installation_token(
        install.installation_id, settings=settings, client=client
    )
    owner, name = _split_full_name(repo.full_name)
    response = await _request(
        "GET",
        f"/repos/{owner}/{name}/actions/secrets/public-key",
        token=token,
        client=client,
    )
    _raise_for(response)
    data = response.json() or {}
    key_id = data.get("key_id")
    key_b64 = data.get("key")
    if not isinstance(key_id, str) or not isinstance(key_b64, str):
        raise WorkflowDispatchError(
            502,
            "GitHub actions/secrets/public-key response missing key_id or key",
        )
    return RepoPublicKey(key_id=key_id, key_b64=key_b64)


async def put_repo_secret(
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    name: str,
    plaintext: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    public_key: RepoPublicKey | None = None,
) -> str:
    """Create or update ``secrets.<name>`` on ``repo``. Returns ``key_id``.

    ``public_key`` lets callers that already fetched the key skip
    the extra RTT (e.g. bulk sync); the helper fetches one lazily
    otherwise. The returned ``key_id`` is what the caller persists
    on the :class:`RepoSecret` row so later ops ("did GitHub rotate
    the repo key since we last wrote?") can compare without a
    second network call.

    GitHub responds 201 Created for a new slot and 204 No Content
    for an update; we accept both and let ``_raise_for`` reject
    anything ≥ 400.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("put_repo_secret requires a non-empty name")
    if len(name) > _MAX_SECRET_NAME:
        raise ValueError(
            f"secret name exceeds GitHub limit ({_MAX_SECRET_NAME} chars)"
        )
    if not isinstance(plaintext, str):
        raise TypeError("put_repo_secret requires a string plaintext")

    key = public_key or await get_repo_public_key(
        repo, install, settings=settings, client=client
    )
    encrypted_value = key.encrypt(plaintext)

    token = await fetch_installation_token(
        install.installation_id, settings=settings, client=client
    )
    owner, repo_name = _split_full_name(repo.full_name)
    response = await _request(
        "PUT",
        f"/repos/{owner}/{repo_name}/actions/secrets/{name}",
        token=token,
        client=client,
        json={"encrypted_value": encrypted_value, "key_id": key.key_id},
    )
    _raise_for(response)
    return key.key_id


async def delete_repo_secret(
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    name: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Delete ``secrets.<name>`` from ``repo``.

    Returns ``True`` if GitHub actually removed a row, ``False``
    if the slot was already empty. 404 is treated as "already
    gone" rather than an error — makes the UI's delete button
    idempotent against double-clicks and re-sync races.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("delete_repo_secret requires a non-empty name")
    token = await fetch_installation_token(
        install.installation_id, settings=settings, client=client
    )
    owner, repo_name = _split_full_name(repo.full_name)
    response = await _request(
        "DELETE",
        f"/repos/{owner}/{repo_name}/actions/secrets/{name}",
        token=token,
        client=client,
    )
    if response.status_code == 404:
        return False
    _raise_for(response)
    return True


__all__ = [
    "RepoPublicKey",
    "delete_repo_secret",
    "get_repo_public_key",
    "put_repo_secret",
]
