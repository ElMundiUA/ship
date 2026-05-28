"""Ticket ref hydration and comment shaping for Navigator + inbox seeds."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from backend.app.integrations.gateway.tracker import CommentRef, TicketRef

_MAX_TICKET_COMMENTS = 20
_TICKET_COMMENTS_CAP_BYTES = 6 * 1024
_PER_COMMENT_BODY_CHARS = 4000


def vendor_kind_to_ticket_kind(
    vendor_kind: str,
) -> Literal["github_issues", "linear", "notion", "jira"]:
    if vendor_kind == "linear":
        return "linear"
    if vendor_kind == "github_issues":
        return "github_issues"
    if vendor_kind == "jira":
        return "jira"
    return "github_issues"


def ticket_ref_from(vendor_kind: str, raw: str) -> TicketRef:
    """Map a human ``ticket_ref`` string to a :class:`TicketRef`."""
    return TicketRef(
        kind=vendor_kind_to_ticket_kind(vendor_kind),
        workspace_hint=None,
        id=raw,
    )


def _comment_attr(comment: Any, key: str) -> Any:
    if isinstance(comment, dict):
        return comment.get(key)
    return getattr(comment, key, None)


def _truncate_body(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def comment_to_row(comment: Any) -> dict[str, Any]:
    created = _comment_attr(comment, "created_at")
    if isinstance(created, datetime):
        created_at = created.isoformat()
    elif created is not None:
        created_at = str(created)
    else:
        created_at = None
    body = str(_comment_attr(comment, "body") or "")
    return {
        "id": str(_comment_attr(comment, "id") or ""),
        "body": _truncate_body(body, _PER_COMMENT_BODY_CHARS),
        "author": _comment_attr(comment, "author"),
        "created_at": created_at,
    }


def serialize_ticket_comments(
    comments: list[Any],
    *,
    max_count: int = _MAX_TICKET_COMMENTS,
    cap_bytes: int = _TICKET_COMMENTS_CAP_BYTES,
) -> tuple[list[dict[str, Any]], bool]:
    """JSON rows for ``ticket_get`` — oldest first, capped."""
    if not comments:
        return [], False
    truncated = len(comments) > max_count
    slice_ = comments[-max_count:] if truncated else list(comments)
    rows = [comment_to_row(c) for c in slice_]
    joined = "\n\n".join(r["body"] for r in rows if r["body"])
    if len(joined.encode("utf-8")) > cap_bytes:
        trimmed = joined.encode("utf-8")[:cap_bytes].decode("utf-8", "ignore")
        # Drop oldest rows until the joined bodies fit the byte cap.
        while rows and len(
            "\n\n".join(r["body"] for r in rows).encode("utf-8")
        ) > cap_bytes:
            rows.pop(0)
            truncated = True
        if not rows and trimmed:
            rows = [
                {
                    "id": "",
                    "body": trimmed + "\n\n…(truncated)",
                    "author": None,
                    "created_at": None,
                }
            ]
            truncated = True
    return rows, truncated


def format_comments_markdown(
    comments: list[Any],
    *,
    max_count: int = _MAX_TICKET_COMMENTS,
    cap_bytes: int = _TICKET_COMMENTS_CAP_BYTES,
) -> str | None:
    """Markdown block for inbox discuss seeds; ``None`` when empty."""
    rows, truncated = serialize_ticket_comments(
        comments, max_count=max_count, cap_bytes=cap_bytes
    )
    if not rows:
        return None
    lines = ["### Recent Linear comments", ""]
    for row in rows:
        ts = row.get("created_at") or "?"
        author = row.get("author") or "unknown"
        body = (row.get("body") or "").strip()
        if not body:
            continue
        lines.append(f"**{ts}** — {author}")
        lines.append("")
        lines.append(body)
        lines.append("")
    if truncated:
        lines.append("_(older comments omitted — thread truncated)_")
        lines.append("")
    if len(lines) <= 2:
        return None
    return "\n".join(lines).rstrip()


__all__ = [
    "CommentRef",
    "TicketRef",
    "_MAX_TICKET_COMMENTS",
    "_TICKET_COMMENTS_CAP_BYTES",
    "format_comments_markdown",
    "serialize_ticket_comments",
    "ticket_ref_from",
    "vendor_kind_to_ticket_kind",
]
