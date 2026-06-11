"""Pin the workspace-policies injection in ``TopicService.assemble_messages``.

The Workspace policy injection feature renders enabled
:class:`WorkspacePolicy` rows as a markdown preamble and slips it
in just after the static ``_AGENT_SYSTEM_PROMPT``. These tests
make sure:

1. With no policies the message list keeps its old shape (system
   prompt first, no extra system message).
2. With at least one enabled policy a second system message
   appears at index 1, ahead of any topic_summary / bucket / kb
   layer.
3. Disabled rows never show up in the rendered preamble even when
   they sort first.
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.db.models.agent_surface import ChatThread
from backend.app.db.models.policies import WorkspacePolicy
from backend.app.services.agent.topic import TopicService


def _make_thread(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    topic_summary: str | None = None,
):
    return ChatThread(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        title="t",
        topic_summary=topic_summary,
    )


@pytest.mark.asyncio
async def test_assemble_no_policies(db_session, seed_workspace) -> None:
    user, _, workspace = seed_workspace
    service = TopicService(
        db_session,
        settings=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=user.id,
    )
    thread = _make_thread(workspace.id, user.id)
    out = await service.assemble_messages(
        thread=thread,
        recent_messages=[],
        new_user_message="hi",
    )
    # system prompt + session-context frame + autonomy block (ELS-244)
    # + final user turn.
    assert len(out) == 4
    assert out[0].role == "system"
    assert "You are Ship" in out[0].content
    assert out[1].role == "system"
    assert "## Session context" in out[1].content
    assert out[2].role == "system"
    assert "Autonomy profile" in out[2].content
    assert out[-1].role == "user"


@pytest.mark.asyncio
async def test_assemble_injects_enabled_policies(
    db_session, seed_workspace
) -> None:
    user, _, workspace = seed_workspace
    db_session.add_all(
        [
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Always work via PR",
                body="Never push directly to main.",
                enabled=True,
                sort_order=0,
            ),
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Never commit secrets",
                body="Use repo Actions secrets instead.",
                enabled=True,
                sort_order=1,
            ),
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Disabled rule",
                body="Should not render.",
                enabled=False,
                sort_order=-100,
            ),
        ]
    )
    await db_session.flush()

    service = TopicService(
        db_session,
        settings=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=user.id,
    )
    thread = _make_thread(
        workspace.id, user.id, topic_summary="Earlier we talked about X."
    )
    out = await service.assemble_messages(
        thread=thread,
        recent_messages=[],
        new_user_message="hi",
    )

    # Layering: 0=system prompt, 1=session context (date + workspace),
    # 2=policies preamble, 3=autonomy block (ELS-244), 4=topic summary,
    # then the live user turn.
    assert out[0].role == "system" and "You are Ship" in out[0].content
    assert out[1].role == "system"
    assert "## Session context" in out[1].content

    assert out[2].role == "system"
    preamble = out[2].content
    assert preamble.startswith("# Workspace policies")
    assert "## Always work via PR" in preamble
    assert "## Never commit secrets" in preamble
    assert "Disabled rule" not in preamble
    assert "Should not render." not in preamble

    assert out[3].role == "system"
    assert "Autonomy profile" in out[3].content

    assert out[4].role == "system"
    assert "Running topic summary" in out[4].content

    assert out[-1].role == "user"
    assert out[-1].content == "hi"
