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
        # Post-#229 the role prompts pass sections via JSON
        # (``"section": "<name>"``) on the finish payload rather than
        # the older ``upsert_project_section(section="<name>")`` tool
        # call, so the test asserts the JSON shape.
        #
        # ``developer`` is special — the ``tasks`` stage sends
        # ``child_tickets`` (not ``project_sections``) and the server
        # auto-renders the ``## Tasks`` section from the created
        # identifiers. The prompt mentions ``Tasks`` only in prose,
        # not in a wire-shape JSON literal. We assert the prose
        # mention instead so a rename of ``Tasks`` to something else
        # still slips into a test failure.
        if slug == "developer":
            assert "Tasks" in prompt, (
                f"{slug}: Decomposition block must mention the "
                f"``Tasks`` section (server auto-renders it from "
                f"``child_tickets``); a rename should slip into a "
                f"test failure."
            )
        else:
            assert f'"section": "{section}"' in prompt, (
                f"{slug}: Decomposition block should pin its owned "
                f'section to ``"section": "{section}"`` so the '
                f"prompt and the tracker helper agree on the heading"
            )


def test_planning_process_config_emits_routines_with_fsm_stage() -> None:
    """ELS-79 — decomposition is now cron-driven (symmetric to SDLC).

    Each non-terminal stage gets a routine in
    ``default_planning_process_config()['routines']`` carrying an
    explicit ``fsm_stage``. ``shipctl run --routine wbs`` reads
    ``fsm_stage: wbs`` and queries ``GET /tracker/next?state=wbs``,
    overriding the BA role's SDLC-default ``ba_requirements``.

    ``planning_done`` is terminal and gets no routine — the finish
    hook on ``tasks`` flips Drafts → Parked.
    """
    from backend.app.services.catalog import default_planning_process_config

    cfg = default_planning_process_config()
    routines = cfg.get("routines") or {}
    assert isinstance(routines, dict)

    expected = {
        "wbs": ("ba", "wbs"),
        "architecture": ("tech-architect", "architecture"),
        "test_architecture": ("qa-architect", "test_architecture"),
        "tasks": ("developer", "tasks"),
    }
    for routine_id, (specialist, fsm_stage) in expected.items():
        routine = routines.get(routine_id)
        assert isinstance(routine, dict), (
            f"missing decomposition routine: {routine_id}"
        )
        assert routine.get("specialist") == specialist, (
            f"{routine_id}.specialist should be {specialist!r}, "
            f"got {routine.get('specialist')!r}"
        )
        assert routine.get("fsm_stage") == fsm_stage, (
            f"{routine_id}.fsm_stage should be {fsm_stage!r}, "
            f"got {routine.get('fsm_stage')!r}"
        )

    # ``planning_done`` is terminal — explicitly NOT a routine.
    assert "planning_done" not in routines, (
        "``planning_done`` is the terminal stage; it must not have a "
        "routine — the finish hook on ``tasks`` triggers the Drafts "
        "→ Parked flip without an extra agent run."
    )
