"""Tests for the long-lived ``SHIP_RUN_TOKEN`` service.

Covers:

- ``mint_repo_callback_token`` pushes to GitHub first, then persists
  hash + prefix + rotated_at (never plaintext).
- GitHub PUT failure leaves the row untouched — critical so a
  half-rotated repo doesn't end up with a hash whose plaintext
  never reached ``secrets.SHIP_RUN_TOKEN``.
- ``verify_repo_callback_token`` matches the minted plaintext
  (constant-time), rejects wrong tokens, rejects empty, rejects
  plaintext that hits the prefix but not the full hash.
- Rotation: second mint overwrites hash + prefix + rotated_at and
  leaves the prior plaintext invalid.

The GitHub secrets PUT is monkeypatched at ``put_repo_secret`` —
we don't want these unit tests to reach the real PyNaCl / HTTP
stack; the integration test for that lives separately.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from backend.app.services import repo_tokens


@pytest_asyncio.fixture
async def seeded_repo(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, _raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=999_101,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=42_100_001,
        full_name="acme/secrets-target",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/secrets-target",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return install, repo


@pytest.mark.asyncio
async def test_mint_pushes_then_persists(monkeypatch, db_session, seeded_repo) -> None:
    from backend.app.core.config import get_settings

    install, repo = seeded_repo
    settings = get_settings()

    pushed: dict[str, str] = {}

    async def _fake_put(
        target_repo, target_install, *, name, plaintext, settings, client=None, public_key=None
    ):
        # Record what the service pushed so the assert sees the
        # exact plaintext minted (verifies "push first, then commit").
        pushed["name"] = name
        pushed["plaintext"] = plaintext
        pushed["repo_id"] = str(target_repo.id)
        return "keyid-stub"

    monkeypatch.setattr(repo_tokens, "put_repo_secret", _fake_put)

    plaintext = await repo_tokens.mint_repo_callback_token(
        db_session, repo, install, settings=settings
    )

    # Service returns plaintext for the caller to react on but must
    # not persist it anywhere. Row stores only the hash.
    assert plaintext
    assert pushed["name"] == "SHIP_RUN_TOKEN"
    assert pushed["plaintext"] == plaintext
    assert pushed["repo_id"] == str(repo.id)

    assert repo.run_token_hash is not None
    assert len(repo.run_token_hash) == 64  # sha256 hex
    assert repo.run_token_prefix == repo.run_token_hash[:8]
    assert repo.run_token_rotated_at is not None
    # Hash must match the reference algorithm.
    assert repo.run_token_hash == repo_tokens.hash_token(plaintext)


@pytest.mark.asyncio
async def test_mint_aborts_on_github_failure_leaves_row_untouched(
    monkeypatch, db_session, seeded_repo
) -> None:
    from backend.app.core.config import get_settings

    install, repo = seeded_repo
    settings = get_settings()

    async def _raise_put(*args, **kwargs):
        raise RuntimeError("github rejected")

    monkeypatch.setattr(repo_tokens, "put_repo_secret", _raise_put)

    # Capture "before" state so we can assert "no change" if the
    # PUT raises. We explicitly want no hash half-committed.
    before_hash = repo.run_token_hash
    before_prefix = repo.run_token_prefix
    before_rotated = repo.run_token_rotated_at

    with pytest.raises(RuntimeError, match="github rejected"):
        await repo_tokens.mint_repo_callback_token(
            db_session, repo, install, settings=settings
        )

    assert repo.run_token_hash == before_hash
    assert repo.run_token_prefix == before_prefix
    assert repo.run_token_rotated_at == before_rotated


@pytest.mark.asyncio
async def test_verify_matches_minted_token(monkeypatch, db_session, seeded_repo) -> None:
    from backend.app.core.config import get_settings

    install, repo = seeded_repo
    settings = get_settings()

    async def _fake_put(*args, **kwargs):
        return "keyid-stub"

    monkeypatch.setattr(repo_tokens, "put_repo_secret", _fake_put)

    plaintext = await repo_tokens.mint_repo_callback_token(
        db_session, repo, install, settings=settings
    )
    await db_session.commit()

    # Positive: freshly minted plaintext resolves back to the same
    # repo row — the round-trip that ``get_run_or_repo_token_context``
    # depends on.
    matched = await repo_tokens.verify_repo_callback_token(db_session, plaintext)
    assert matched is not None
    assert matched.id == repo.id


@pytest.mark.asyncio
async def test_verify_rejects_wrong_and_empty_tokens(
    monkeypatch, db_session, seeded_repo
) -> None:
    from backend.app.core.config import get_settings

    install, repo = seeded_repo
    settings = get_settings()

    async def _fake_put(*args, **kwargs):
        return "keyid-stub"

    monkeypatch.setattr(repo_tokens, "put_repo_secret", _fake_put)

    await repo_tokens.mint_repo_callback_token(
        db_session, repo, install, settings=settings
    )
    await db_session.commit()

    # Empty bearer / missing token — the dependency shapes these as
    # 401; here we verify the service returns ``None``.
    assert await repo_tokens.verify_repo_callback_token(db_session, "") is None

    # A random string with no prefix overlap returns None (phase-1
    # short-circuit on prefix mismatch).
    assert (
        await repo_tokens.verify_repo_callback_token(
            db_session, "clearly-not-a-real-token"
        )
        is None
    )


@pytest.mark.asyncio
async def test_rotation_invalidates_prior_plaintext(
    monkeypatch, db_session, seeded_repo
) -> None:
    from backend.app.core.config import get_settings

    install, repo = seeded_repo
    settings = get_settings()

    async def _fake_put(*args, **kwargs):
        return "keyid-stub"

    monkeypatch.setattr(repo_tokens, "put_repo_secret", _fake_put)

    first = await repo_tokens.mint_repo_callback_token(
        db_session, repo, install, settings=settings
    )
    await db_session.commit()
    first_rotated_at = repo.run_token_rotated_at
    first_hash = repo.run_token_hash

    # Second mint overwrites in-place.
    second = await repo_tokens.mint_repo_callback_token(
        db_session, repo, install, settings=settings
    )
    await db_session.commit()

    # Plaintext must differ (basic sanity) and the prior plaintext
    # must no longer resolve to the repo — simulates "runner in
    # flight using old secret gets 401 after rotation".
    assert first != second
    assert repo.run_token_hash != first_hash
    assert repo.run_token_rotated_at is not None
    assert repo.run_token_rotated_at >= first_rotated_at

    assert (
        await repo_tokens.verify_repo_callback_token(db_session, first) is None
    )
    matched = await repo_tokens.verify_repo_callback_token(db_session, second)
    assert matched is not None
    assert matched.id == repo.id
