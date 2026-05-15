"""Eval runner — score every artifact under a run-id with both judges.

Layout the runner expects:

    tools/eval/runs/<run_id>/
        planning.json           # one artifact per routine
        decomposition.json
        dev.json
        validation.json
        self-heal.json

    tools/eval/rubrics/
        planning.md
        decomposition.md
        dev.md
        validation.md
        self-heal.md            # rubric name == artifact basename

Output:

    tools/eval/results/<run_id>/
        planning.claude.json    # full JudgeResult for the Claude pass
        planning.gpt.json       # ditto for GPT-5 mini
        ...
    tools/eval/metrics.jsonl    # one row per (run_id, routine, model)
                                  appended on every run for trend tracking

Cost report prints to stdout — per-routine, per-model, plus a total
for the whole run. The total is the "stoimost' odnogo progona" line.

Usage:

    OPENAI_API_KEY=… ANTHROPIC_API_KEY=… \\
      PYTHONPATH=. .venv/bin/python tools/eval/judge.py \\
        --run-id 2026-05-15T14-22-31Z

    # or default to the most recent run-id under runs/
    OPENAI_API_KEY=… ANTHROPIC_API_KEY=… \\
      PYTHONPATH=. .venv/bin/python tools/eval/judge.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tools.eval.judges import JudgeError, JudgeRequest, JudgeResult
from tools.eval.judges import claude as claude_judge
from tools.eval.judges import openai_judge as gpt_judge


ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = ROOT / "tools" / "eval"
RUNS = EVAL_ROOT / "runs"
RESULTS = EVAL_ROOT / "results"
RUBRICS = EVAL_ROOT / "rubrics"
METRICS = EVAL_ROOT / "metrics.jsonl"


# Map artifact basename → rubric file. The artifact dumper uses these
# canonical routine names; if you add a new one, register here.
ROUTINE_TO_RUBRIC: dict[str, str] = {
    "planning": "planning.md",
    "decomposition": "decomposition.md",
    "dev": "dev.md",
    "dev_implementation": "dev.md",
    "validation": "validation.md",
    "self_heal": "self-heal.md",
    "self-heal": "self-heal.md",
    "devops": "devops.md",
    "devops_implementation": "devops.md",
}


# Judge configs. Each entry: (callable, model_id).
JUDGES: list[tuple[str, Any, str]] = [
    ("claude", claude_judge.run, "claude-sonnet-4-6"),
    ("gpt", gpt_judge.run, "gpt-5-mini"),
]


def _pick_run_id(explicit: str | None) -> str:
    if explicit:
        return explicit
    candidates = sorted(
        (p for p in RUNS.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if RUNS.is_dir() else []
    if not candidates:
        raise SystemExit(
            f"no runs found under {RUNS} — capture artifacts first "
            "via the pipeline e2e suite"
        )
    return candidates[0].name


def _load_rubric(routine: str) -> str:
    rubric_file = ROUTINE_TO_RUBRIC.get(routine)
    if rubric_file is None:
        raise SystemExit(
            f"no rubric mapping for routine {routine!r} — register in "
            "tools/eval/judge.py:ROUTINE_TO_RUBRIC"
        )
    path = RUBRICS / rubric_file
    if not path.is_file():
        raise SystemExit(f"rubric file missing: {path}")
    return path.read_text(encoding="utf-8")


def _format_cost(usd: float) -> str:
    if usd >= 0.01:
        return f"${usd:.4f}"
    return f"${usd * 100:.3f}¢"


def _human_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _summarise(result: JudgeResult) -> str:
    t = result.tokens
    cost = _format_cost(result.cost_usd)
    cached_pct = (
        100 * t.cache_read / max(1, t.input_uncached + t.cache_read + t.cache_write)
    )
    badge = "✓" if result.would_ship else "✗"
    failures = (
        f" ⚠ {', '.join(result.failures)}" if result.failures else ""
    )
    return (
        f"  {result.model:24} score={result.score:5.1f} {badge:1} "
        f"cost={cost:>10}  "
        f"tok(in={_human_tokens(t.input_uncached)}/cache_r={_human_tokens(t.cache_read)}"
        f"/cache_w={_human_tokens(t.cache_write)}/out={_human_tokens(t.output)})"
        f"  cache_hit={cached_pct:3.0f}%  {result.latency_ms}ms{failures}"
    )


def _write_result(run_id: str, routine: str, label: str, result: JudgeResult) -> None:
    out_dir = RESULTS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{routine}.{label}.json"
    payload = asdict(result)
    # Replace dataclass for the tokens sub-dict — asdict already
    # handles it but the SDK objects above don't need extra coercion.
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _append_metric(
    run_id: str, routine: str, label: str, result: JudgeResult
) -> None:
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "run_id": run_id,
        "routine": routine,
        "judge": label,
        "model": result.model,
        "score": result.score,
        "would_ship": result.would_ship,
        "cost_usd": result.cost_usd,
        "tokens": asdict(result.tokens),
        "latency_ms": result.latency_ms,
        "improvements": [asdict(i) for i in result.improvements],
        "failures": result.failures,
    }
    with METRICS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run id to score (defaults to most-recent run under tools/eval/runs/)",
    )
    parser.add_argument(
        "--routines",
        nargs="*",
        default=None,
        help="Subset of routine names to score; default is all artifacts present.",
    )
    parser.add_argument(
        "--judges",
        nargs="*",
        default=None,
        choices=[label for label, _, _ in JUDGES],
        help="Subset of judges to invoke; default is all.",
    )
    args = parser.parse_args()

    run_id = _pick_run_id(args.run_id)
    run_dir = RUNS / run_id
    if not run_dir.is_dir():
        raise SystemExit(f"no such run id: {run_dir}")

    artifacts = sorted(run_dir.glob("*.json"))
    if not artifacts:
        raise SystemExit(f"no artifacts in {run_dir}")

    judges = [
        entry for entry in JUDGES if not args.judges or entry[0] in args.judges
    ]

    if not os.environ.get("ANTHROPIC_API_KEY") and any(
        label == "claude" for label, _, _ in judges
    ):
        print("WARN: ANTHROPIC_API_KEY not set; Claude judge will fail.", file=sys.stderr)
    if not os.environ.get("OPENAI_API_KEY") and any(
        label == "gpt" for label, _, _ in judges
    ):
        print("WARN: OPENAI_API_KEY not set; GPT judge will fail.", file=sys.stderr)

    print(f"=== Eval run {run_id} ===")
    print(f"artifacts: {len(artifacts)}  judges: {[l for l, _, _ in judges]}")
    print()

    totals_by_judge: dict[str, float] = {label: 0.0 for label, _, _ in judges}
    totals_by_judge_tokens: dict[str, TokenBreakdownAcc] = {
        label: TokenBreakdownAcc() for label, _, _ in judges
    }
    routine_summaries: list[tuple[str, list[JudgeResult]]] = []

    for art_path in artifacts:
        routine = art_path.stem
        if args.routines and routine not in args.routines:
            continue
        try:
            artifact = json.loads(art_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"SKIP {routine}: artifact JSON invalid — {exc}")
            continue

        rubric_text = _load_rubric(routine)
        print(f"--- {routine} ({art_path.name}) ---")

        run_results: list[JudgeResult] = []
        for label, judge_fn, model in judges:
            req = JudgeRequest(
                routine=routine,
                rubric=rubric_text,
                artifact=artifact,
                model=model,
            )
            try:
                result = judge_fn(req)
            except JudgeError as exc:
                print(f"  {model:24} ERROR: {exc}")
                continue
            print(_summarise(result))
            _write_result(run_id, routine, label, result)
            _append_metric(run_id, routine, label, result)
            totals_by_judge[label] += result.cost_usd
            totals_by_judge_tokens[label].add(result.tokens)
            run_results.append(result)
        routine_summaries.append((routine, run_results))
        print()

    # ---- final cost summary -----------------------------------------

    print("=== Total cost ===")
    grand = 0.0
    for label, total in totals_by_judge.items():
        acc = totals_by_judge_tokens[label]
        grand += total
        cached_pct = acc.cache_read_pct()
        print(
            f"  {label:8} {_format_cost(total):>10}  "
            f"in={_human_tokens(acc.input_uncached)} "
            f"cache_r={_human_tokens(acc.cache_read)} "
            f"cache_w={_human_tokens(acc.cache_write)} "
            f"out={_human_tokens(acc.output)}  "
            f"cache_hit={cached_pct:3.0f}%"
        )
    print(f"  {'TOTAL':8} {_format_cost(grand):>10}")
    print()

    # ---- ship-readiness verdict per routine -------------------------

    print("=== Ship verdict ===")
    for routine, results in routine_summaries:
        if not results:
            print(f"  {routine:18} (no judges ran)")
            continue
        votes_ship = sum(1 for r in results if r.would_ship)
        scores = ", ".join(f"{r.model.split('-')[0]}={r.score:.1f}" for r in results)
        verdict = (
            "ship" if votes_ship == len(results)
            else "split" if votes_ship > 0
            else "hold"
        )
        print(f"  {routine:18} [{verdict:5}]  votes={votes_ship}/{len(results)}  {scores}")
    print()

    # ---- prompt-tuning suggestions ---------------------------------
    # Surface the judges' improvement notes inline so the operator
    # doesn't have to open each result JSON to find them.

    any_improvements = any(
        r.improvements for _, results in routine_summaries for r in results
    )
    if any_improvements:
        print("=== Suggested prompt edits ===")
        for routine, results in routine_summaries:
            for r in results:
                if not r.improvements:
                    continue
                judge_label = r.model.split("-")[0]
                print(f"  {routine} (judge: {judge_label}, score {r.score:.1f}):")
                for imp in r.improvements:
                    print(
                        f"    [{imp.criterion}] +{imp.expected_lift_pts}pts — {imp.issue}"
                    )
                    # Wrap the suggested edit at ~76 cols so it
                    # stays readable on a typical terminal.
                    edit_lines = [
                        imp.suggested_prompt_edit[i : i + 76]
                        for i in range(0, len(imp.suggested_prompt_edit), 76)
                    ]
                    for line in edit_lines:
                        print(f"      → {line}")
                print()
    else:
        print(
            "=== Suggested prompt edits ===\n  (judges returned no "
            "improvement notes — likely scores at 95+ across the board)\n"
        )

    return 0


class TokenBreakdownAcc:
    """Running sum of TokenBreakdowns across one judge's calls in a
    single eval run. Inlined here to keep judges/base.py free of
    accumulator concerns."""

    __slots__ = ("input_uncached", "cache_write", "cache_read", "output")

    def __init__(self) -> None:
        self.input_uncached = 0
        self.cache_write = 0
        self.cache_read = 0
        self.output = 0

    def add(self, t: Any) -> None:
        self.input_uncached += t.input_uncached
        self.cache_write += t.cache_write
        self.cache_read += t.cache_read
        self.output += t.output

    def cache_read_pct(self) -> float:
        denom = self.input_uncached + self.cache_read + self.cache_write
        return 100 * self.cache_read / denom if denom else 0.0


if __name__ == "__main__":
    sys.exit(main())
