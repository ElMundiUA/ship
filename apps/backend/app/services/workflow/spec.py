"""Workflow definition language + loader (W8.1, ELS-256).

A workflow spec lives at ``.ship/workflows/<name>.yaml`` in the
customer repo and is DECLARATIVE and BOUNDED: a DAG with a fan-out
cap, no unbounded recursion, no event subscription — it is INVOKED
and COMPLETES. Seven step kinds:

- ``parallel``  — fan out the nested ``steps`` concurrently;
- ``barrier``   — join point: waits for everything in ``needs``;
- ``pipeline``  — leaf chained behind ``needs``, prior outputs
  available to its inputs via ``{{ steps.<id>.output.<key> }}``;
- ``loop``      — repeat a leaf until ``until`` or ``max_iters``;
- ``synthesize`` / ``judge`` / ``verify`` — reasoning steps with a
  fixed role flavor that consume prior outputs and MUST emit a
  structured object validated against their inline ``output_schema``.

Budgets mirror the subagent convention (``_SUBAGENT_MAX_TOOL_CALLS=25``
/ ``_SUBAGENT_MAX_SECONDS=300`` in :mod:`services.agent.tools`).
``max_fanout`` (default 4, hard ceiling 8) and ``max_depth`` (default
2) are enforced AT LOAD TIME, before any dispatch, so a malformed
spec can't even request a fork-bomb.
"""

from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# Hard ceilings — the loader refuses anything above these regardless
# of what the spec declares. Control plane values, not preferences:
# no autonomy profile loosens them.
HARD_FANOUT_CEILING = 8
DEFAULT_MAX_FANOUT = 4
DEFAULT_MAX_DEPTH = 2

STEP_KINDS = (
    "parallel",
    "pipeline",
    "loop",
    "barrier",
    "synthesize",
    "judge",
    "verify",
)
# Step kinds whose leaf is a fixed-role reasoning turn and whose
# output MUST validate against an inline JSON Schema.
STRUCTURED_KINDS = ("synthesize", "judge", "verify")

CODING_PROVIDERS = ("claude", "codex", "cursor", "ship")
REASONING_PROVIDER = "reasoning"


class WorkflowSpecError(ValueError):
    """Raised by the loader for any invalid spec — always names the
    offending step when one exists."""


class AgentLeaf(BaseModel):
    """Leaf executor selection (T5: spawn, never re-implement).

    ``kind=coding`` spawns a tool CLI via ``runAgent`` (claude / codex
    / cursor / ship); ``kind=reasoning`` runs an in-process Navigator
    subagent turn, role-prompted.
    """

    kind: Literal["coding", "reasoning"]
    provider: str = REASONING_PROVIDER
    role: str | None = None
    prompt: str | None = None

    @model_validator(mode="after")
    def _provider_matches_kind(self) -> "AgentLeaf":
        if self.kind == "coding":
            if self.provider not in CODING_PROVIDERS:
                raise ValueError(
                    f"unknown coding provider '{self.provider}' "
                    f"(known: {', '.join(CODING_PROVIDERS)})"
                )
        else:
            if self.provider not in (REASONING_PROVIDER,):
                raise ValueError(
                    "reasoning leaves use provider 'reasoning' "
                    f"(got '{self.provider}')"
                )
        return self


class StepSpec(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    kind: str
    agent: AgentLeaf | None = None
    # Children — parallel only.
    steps: list["StepSpec"] = Field(default_factory=list)
    # DAG edges: ids of steps that must complete first. A barrier
    # joins exclusively via needs.
    needs: list[str] = Field(default_factory=list)
    # Static inputs + ``{{ steps.<id>.output.<key> }}`` templates.
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    # loop only.
    until: str | None = None
    max_iters: int = Field(default=3, ge=1, le=10)
    # Budget convention from services/agent/tools.py.
    max_tool_calls: int = Field(default=25, ge=1, le=100)
    max_seconds: int = Field(default=300, ge=10, le=1800)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        if v not in STEP_KINDS:
            raise ValueError(
                f"unknown step kind '{v}' (known: {', '.join(STEP_KINDS)})"
            )
        return v

    @model_validator(mode="after")
    def _shape_for_kind(self) -> "StepSpec":
        if self.kind in STRUCTURED_KINDS:
            if not self.output_schema:
                raise ValueError(
                    f"step '{self.id}': {self.kind} requires output_schema"
                )
            if (
                not isinstance(self.output_schema, dict)
                or "type" not in self.output_schema
            ):
                raise ValueError(
                    f"step '{self.id}': output_schema must be a JSON Schema "
                    "object carrying 'type'"
                )
            if self.agent is not None and self.agent.kind != "reasoning":
                raise ValueError(
                    f"step '{self.id}': {self.kind} is a reasoning step"
                )
        if self.kind == "parallel":
            if not self.steps:
                raise ValueError(
                    f"step '{self.id}': parallel requires nested steps"
                )
        elif self.steps:
            raise ValueError(
                f"step '{self.id}': only parallel steps nest children"
            )
        if self.kind == "barrier":
            if not self.needs:
                raise ValueError(
                    f"step '{self.id}': barrier requires needs[]"
                )
            if self.agent is not None:
                raise ValueError(
                    f"step '{self.id}': barrier is a join point, no agent"
                )
        if self.kind in ("pipeline", "loop") and self.agent is None:
            raise ValueError(f"step '{self.id}': {self.kind} requires agent")
        return self


class WorkflowSpec(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = "1"
    # Declared input names → type labels (string/int/bool/object) —
    # documentation-grade typing, checked loosely at invocation.
    inputs: dict[str, str] = Field(default_factory=dict)
    max_fanout: int = Field(default=DEFAULT_MAX_FANOUT, ge=1)
    max_depth: int = Field(default=DEFAULT_MAX_DEPTH, ge=1)
    steps: list[StepSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _bounded(self) -> "WorkflowSpec":
        if self.max_fanout > HARD_FANOUT_CEILING:
            raise ValueError(
                f"max_fanout={self.max_fanout} exceeds the hard ceiling "
                f"{HARD_FANOUT_CEILING}"
            )
        # Static depth: nesting levels of parallel children.
        def depth(steps: list[StepSpec], level: int) -> int:
            deepest = level
            for s in steps:
                if s.steps:
                    deepest = max(deepest, depth(s.steps, level + 1))
            return deepest

        actual_depth = depth(self.steps, 1)
        if actual_depth > self.max_depth:
            raise ValueError(
                f"static step graph depth {actual_depth} exceeds "
                f"max_depth={self.max_depth}"
            )
        # Fan-out: no parallel step may declare more children than
        # max_fanout.
        def check_fanout(steps: list[StepSpec]) -> None:
            for s in steps:
                if s.kind == "parallel" and len(s.steps) > self.max_fanout:
                    raise ValueError(
                        f"step '{s.id}': fan-out {len(s.steps)} exceeds "
                        f"max_fanout={self.max_fanout}"
                    )
                check_fanout(s.steps)

        check_fanout(self.steps)
        # Unique ids + resolvable needs edges.
        ids: set[str] = set()

        def collect(steps: list[StepSpec]) -> None:
            for s in steps:
                if s.id in ids:
                    raise ValueError(f"duplicate step id '{s.id}'")
                ids.add(s.id)
                collect(s.steps)

        collect(self.steps)

        def check_needs(steps: list[StepSpec]) -> None:
            for s in steps:
                for need in s.needs:
                    if need not in ids:
                        raise ValueError(
                            f"step '{s.id}': needs unknown step '{need}'"
                        )
                check_needs(s.steps)

        check_needs(self.steps)
        return self


def load_spec(text: str) -> WorkflowSpec:
    """Parse + validate one ``.ship/workflows/*.yaml`` document.

    Raises :class:`WorkflowSpecError` with the offending step named
    for every rejection — the loader runs BEFORE any dispatch, so a
    malformed spec can never reach the gate."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowSpecError(f"invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowSpecError("workflow spec must be a YAML mapping")
    try:
        return WorkflowSpec.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError → stable error type
        raise WorkflowSpecError(str(exc)) from exc


def validate_output(value: Any, schema: dict[str, Any], *, step_id: str) -> None:
    """Minimal structural JSON-Schema check (no external dependency).

    Supports the subset our structured steps use: ``type``,
    ``properties`` + ``required`` for objects, ``items`` for arrays,
    ``enum``. Raises :class:`WorkflowSpecError` naming the step."""
    def fail(msg: str) -> None:
        raise WorkflowSpecError(f"step '{step_id}': output invalid — {msg}")

    def check(v: Any, s: dict[str, Any], path: str) -> None:
        expected = s.get("type")
        type_map = {
            "object": dict,
            "array": list,
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
        }
        if expected in type_map and not isinstance(v, type_map[expected]):
            fail(f"{path or '$'} expected {expected}, got {type(v).__name__}")
        if expected == "boolean" and isinstance(v, bool) is False:
            fail(f"{path or '$'} expected boolean")
        if "enum" in s and v not in s["enum"]:
            fail(f"{path or '$'} not in enum {s['enum']}")
        if expected == "object":
            for key in s.get("required", []):
                if key not in v:
                    fail(f"{path or '$'} missing required key '{key}'")
            for key, sub in (s.get("properties") or {}).items():
                if key in v and isinstance(sub, dict):
                    check(v[key], sub, f"{path}.{key}")
        if expected == "array" and isinstance(s.get("items"), dict):
            for i, item in enumerate(v):
                check(item, s["items"], f"{path}[{i}]")

    check(value, schema, "")
