"""Decomposition-mode prompt sections on the SDLC role files (ELS-29).

Each role that participates in the decomposition chain — BA,
Tech-architect, QA-architect, QA-engineer (placeholder), Developer —
gets a ``## Decomposition mode`` block in its prompt. The block
documents the project-anchor-vs-ticket distinction and pins which
``## <section>`` of the project body the role owns.

These tests are deliberately structural rather than semantic: the
role corpus is markdown driven by humans, and asserting on the
prompt text would lock in editorial choices that should be free to
evolve. We assert the load-bearing invariants only:

- The block exists.
- It explicitly mentions ``process=decomposition`` so a runtime that
  passes that flag triggers the right branch in the agent's reading.
- It pins the role's owned project-body section by name so a human
  editor can't silently rename it without the test catching the drift.

The development-process flow — which lives above the new block in
each role file — keeps its own coverage in the role-loader tests.
"""

from __future__ import annotations

import pytest


# (slug, owned_section_in_project_body) — the section names ride the
# ``upsert_project_section`` contract; keep these in sync with
# ``default_planning_process_config()`` in ``catalog.py``.
_ROLE_SECTION = [
    ("ba", "WBS"),
    ("tech-architect", "Architecture"),
    ("qa-architect", "Test architecture"),
    # qa-engineer is a placeholder — not invoked in the current chain
    # but documented so a future stage can route to it without the
    # prompt going stale.
    ("qa-engineer", None),
    ("developer", "Tasks"),
]


@pytest.mark.parametrize("slug,section", _ROLE_SECTION)
def test_role_carries_decomposition_block(slug: str, section: str | None) -> None:
    from backend.app.services import agent_roles as svc

    role = svc.get_default(slug)
    assert role is not None, f"missing default agent role: {slug}"
    prompt = role.prompt

    assert "## Decomposition mode" in prompt, (
        f"{slug}: role file is missing the Decomposition mode block"
    )
    assert "process=decomposition" in prompt, (
        f"{slug}: Decomposition block must reference ``process=decomposition`` "
        f"so a runtime passing that flag triggers the right branch"
    )

    if section is not None:
        # Pin the owned ``## <name>`` section by literal text so a
        # rename slips into a test failure instead of a silent
        # drift that breaks ``upsert_project_section`` matching.
        assert f'section="{section}"' in prompt, (
            f"{slug}: Decomposition block should pin its owned section to "
            f'``section="{section}"`` so the prompt and the tracker '
            f"helper agree on the heading"
        )
