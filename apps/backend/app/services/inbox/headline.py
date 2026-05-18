"""Derive inbox list headlines (ELS-145).

Every ``inbox_items`` row carries a short ``headline`` (≤80 chars) for
list scanning. Prefer an explicit headline, else the first line of
``summary``, else a truncated ``title``.
"""

from __future__ import annotations

HEADLINE_MAX_LEN = 80


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def derive_headline(
    *,
    headline: str | None = None,
    summary: str | None = None,
    title: str,
) -> str:
    """Return a non-empty headline clamped to :data:`HEADLINE_MAX_LEN`."""
    explicit = (headline or "").strip()
    if explicit:
        source = explicit
    else:
        from_summary = _first_line(summary)
        source = from_summary if from_summary else (title or "").strip()
    if not source:
        source = "Inbox item"
    if len(source) <= HEADLINE_MAX_LEN:
        return source
    return source[:HEADLINE_MAX_LEN]
