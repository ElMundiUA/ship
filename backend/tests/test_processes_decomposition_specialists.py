"""Decomposition specialist mapping (ELS-79 follow-up).

``_build_decomposition_process`` looks up specialists by template-id
via ``_specialists`` (a dict keyed on ``role_templates.py`` ids).
``_specialist_for_lane`` is the fallback that resolves a stage id
to one of those template-ids. Pre-this fix the decomposition
stages weren't in the ``direct`` map, so the substring fallback
caught some (``architecture`` / ``test_architecture`` matched
``arch``) and the rest fell through to ``devops_platform``.
``planning_done`` and ``wbs`` and ``tasks`` then ended up rendering
with the wrong canvas badge and — depending on whether the
template id resolved — could KeyError on the projection's
``specialists[specialist_id].name`` lookup.

Pin the explicit mapping so a future grammar / role bump catches
the regression in CI.
"""

from __future__ import annotations

import pytest

from backend.app.api.v1.routes.processes import (
    _specialist_for_lane,
    _specialists,
)


@pytest.mark.parametrize(
    "stage,expected_template_id",
    [
        ("wbs", "business_analyst"),
        ("architecture", "technical_architect"),
        ("test_architecture", "qa_engineer"),
        ("tasks", "developer"),
        ("planning_done", "developer"),
    ],
)
def test_decomposition_stages_map_to_canon_template_ids(
    stage: str, expected_template_id: str
) -> None:
    assert _specialist_for_lane(stage) == expected_template_id


@pytest.mark.parametrize(
    "stage",
    ["wbs", "architecture", "test_architecture", "tasks", "planning_done"],
)
def test_decomposition_specialist_resolves_in_specialists_dict(stage: str) -> None:
    """Round-trip: lane → template_id → specialist name. The
    projection in ``_build_decomposition_process`` does this lookup
    on every render; if it KeyErrors the dashboard 500s on the
    decomposition page."""
    template_id = _specialist_for_lane(stage)
    specialists = _specialists()
    assert template_id in specialists, (
        f"specialist template_id {template_id!r} for stage {stage!r} "
        f"is not in _specialists; decomposition projection will KeyError"
    )
    # ``.name`` is what the canvas actually renders.
    assert specialists[template_id].name


def test_sdlc_stages_unchanged_by_decomposition_additions() -> None:
    """Adding decomposition stages to the direct map must not break
    the existing SDLC stage mappings."""
    sdlc = {
        "task_intake": "intake",
        "bug_triage": "intake",
        "ba_requirements": "business_analyst",
        "tech_arch_plan": "technical_architect",
        "qa_arch_plan": "qa_engineer",
        "dev_implementation": "developer",
        "qa_manual": "qa_engineer",
        "qa_automation": "qa_engineer",
        "code_review": "code_reviewer",
    }
    for stage, expected in sdlc.items():
        assert _specialist_for_lane(stage) == expected
