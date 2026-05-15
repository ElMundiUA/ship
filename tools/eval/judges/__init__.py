"""Judge backends — one module per provider (claude, openai_judge).

Concrete judges return :class:`JudgeResult` so the runner can
aggregate cost + score across providers without caring about which
SDK produced the row.
"""

from tools.eval.judges.base import (
    JudgeError,
    JudgeRequest,
    JudgeResult,
    TokenBreakdown,
)

__all__ = [
    "JudgeError",
    "JudgeRequest",
    "JudgeResult",
    "TokenBreakdown",
]
