"""Parse tracker ticket refs from PR titles (shared by dispatch + hooks)."""

from __future__ import annotations

import re

# Matches common Linear/Jira keys in PR titles (``feat(ELS-99): …``).
# First match is the primary ref for PR→ticket mapping; additional
# refs are audit-only when a title mentions multiple tickets.
TICKET_REF_PR_TITLE_RE = re.compile(r"\b([A-Z][A-Z0-9]{2,9}-\d+)\b")


def parse_ticket_refs_from_pr_title(title: str) -> list[str]:
    """Return ticket refs found in ``title``, first ref is primary."""
    if not title:
        return []
    return TICKET_REF_PR_TITLE_RE.findall(title)
