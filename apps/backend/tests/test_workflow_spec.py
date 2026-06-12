"""W8.1 (ELS-256) — workflow spec loader.

Covers the AC matrix: a valid full spec with all seven kinds
round-trips; each invalid shape (unknown kind, unknown provider,
missing output_schema, fan-out over ceiling, depth over max) rejects
BEFORE anything runs, naming the offending step; a pipeline's output
templates reference prior step ids.
"""

from __future__ import annotations

import pytest

from backend.app.services.workflow.spec import (
    HARD_FANOUT_CEILING,
    WorkflowSpecError,
    load_spec,
    validate_output,
)


FULL_SPEC = """
name: pr-review
version: "2"
inputs:
  pr_url: string
max_fanout: 4
max_depth: 2
steps:
  - id: fan
    kind: parallel
    steps:
      - id: axis.correctness
        kind: pipeline
        agent: {kind: reasoning, role: reviewer}
        inputs: {axis: correctness, pr_url: "{{ inputs.pr_url }}"}
      - id: axis.security
        kind: pipeline
        agent: {kind: reasoning, role: reviewer}
        inputs: {axis: security}
  - id: join
    kind: barrier
    needs: [axis.correctness, axis.security]
  - id: synth
    kind: synthesize
    needs: [join]
    agent: {kind: reasoning, role: synthesizer}
    inputs: {findings: "{{ steps.axis.correctness.output.findings }}"}
    output_schema:
      type: object
      required: [findings]
      properties:
        findings:
          type: array
          items:
            type: object
            required: [severity, title]
            properties:
              severity: {type: string, enum: [low, medium, high]}
              title: {type: string}
  - id: recheck
    kind: verify
    needs: [synth]
    output_schema: {type: object, required: [verdict], properties: {verdict: {type: string}}}
  - id: deep-dive
    kind: loop
    needs: [synth]
    agent: {kind: coding, provider: claude}
    until: "output.done == true"
    max_iters: 2
  - id: judge-it
    kind: judge
    needs: [deep-dive]
    output_schema: {type: object, required: [rank], properties: {rank: {type: integer}}}
"""


def test_full_spec_round_trips() -> None:
    spec = load_spec(FULL_SPEC)
    assert spec.name == "pr-review"
    assert spec.version == "2"
    kinds = {s.kind for s in spec.steps}
    nested = {s.kind for s in spec.steps[0].steps}
    assert kinds | nested == {
        "parallel", "pipeline", "barrier", "synthesize", "verify", "loop", "judge",
    }
    # Budget defaults follow the subagent convention.
    assert spec.steps[0].max_tool_calls == 25
    assert spec.steps[0].max_seconds == 300
    # Pipeline output template references a prior step.
    assert "steps.axis.correctness.output" in str(
        next(s for s in spec.steps if s.id == "synth").inputs["findings"]
    )


def test_unknown_step_kind_names_step() -> None:
    bad = FULL_SPEC.replace("kind: barrier", "kind: teleport")
    with pytest.raises(WorkflowSpecError, match="teleport"):
        load_spec(bad)


def test_unknown_provider_names_step() -> None:
    bad = FULL_SPEC.replace("provider: claude", "provider: skynet")
    with pytest.raises(WorkflowSpecError, match="skynet"):
        load_spec(bad)


def test_fanout_over_hard_ceiling_rejected() -> None:
    bad = FULL_SPEC.replace(
        "max_fanout: 4", f"max_fanout: {HARD_FANOUT_CEILING + 1}"
    )
    with pytest.raises(WorkflowSpecError, match="hard ceiling"):
        load_spec(bad)


def test_parallel_fanout_over_declared_cap_rejected() -> None:
    spec = """
name: wide
max_fanout: 2
steps:
  - id: fan
    kind: parallel
    steps:
      - {id: a, kind: pipeline, agent: {kind: reasoning}}
      - {id: b, kind: pipeline, agent: {kind: reasoning}}
      - {id: c, kind: pipeline, agent: {kind: reasoning}}
"""
    with pytest.raises(WorkflowSpecError, match="fan-out 3 exceeds"):
        load_spec(spec)


def test_depth_over_max_rejected() -> None:
    spec = """
name: deep
max_depth: 1
steps:
  - id: fan
    kind: parallel
    steps:
      - id: inner
        kind: parallel
        steps:
          - {id: leaf, kind: pipeline, agent: {kind: reasoning}}
"""
    with pytest.raises(WorkflowSpecError, match="depth"):
        load_spec(spec)


def test_synthesize_without_schema_rejected() -> None:
    spec = """
name: x
steps:
  - id: s
    kind: synthesize
"""
    with pytest.raises(WorkflowSpecError, match="output_schema"):
        load_spec(spec)


def test_unknown_needs_edge_rejected() -> None:
    spec = """
name: x
steps:
  - id: s
    kind: barrier
    needs: [ghost]
"""
    with pytest.raises(WorkflowSpecError, match="ghost"):
        load_spec(spec)


def test_validate_output_subset() -> None:
    schema = {
        "type": "object",
        "required": ["findings"],
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["severity"],
                    "properties": {
                        "severity": {"type": "string", "enum": ["low", "high"]}
                    },
                },
            }
        },
    }
    validate_output(
        {"findings": [{"severity": "high"}]}, schema, step_id="synth"
    )
    with pytest.raises(WorkflowSpecError, match="missing required key"):
        validate_output({}, schema, step_id="synth")
    with pytest.raises(WorkflowSpecError, match="enum"):
        validate_output(
            {"findings": [{"severity": "critical"}]}, schema, step_id="synth"
        )


def test_fetch_leaf_accepted_and_normalized() -> None:
    spec = load_spec(
        """
name: ctx
steps:
  - id: diff
    kind: pipeline
    agent: {kind: fetch}
    inputs: {url: "{{ inputs.pr_url }}"}
"""
    )
    assert spec.steps[0].agent.kind == "fetch"
    assert spec.steps[0].agent.provider == "fetch"


def test_fetch_leaf_rejects_foreign_provider() -> None:
    with pytest.raises(WorkflowSpecError, match="fetch leaves"):
        load_spec(
            """
name: ctx
steps:
  - id: diff
    kind: pipeline
    agent: {kind: fetch, provider: claude}
"""
        )
