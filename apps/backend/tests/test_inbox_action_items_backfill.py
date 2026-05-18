"""ELS-165 — unit tests for the legacy clarification → action_items
parser. The integration sweep is exercised by the cron wrapper; this
file only covers the pure markdown parser."""

from __future__ import annotations

import pytest

from backend.app.services.inbox.backfill_action_items import (
    parse_action_items_from_markdown,
)


def test_empty_body_returns_freeform_only() -> None:
    items, mode = parse_action_items_from_markdown("")
    assert items == []
    assert mode == "freeform_only"


def test_no_question_blocks_returns_freeform_only() -> None:
    body = "Plain prose without a Q1 header. Options: **A** / **B**."
    items, mode = parse_action_items_from_markdown(body)
    assert items == []
    assert mode == "freeform_only"


def test_single_question_three_options_returns_single_choice() -> None:
    body = (
        "Auto-merger paused.\n\n"
        "**Q1.** Apply migration on merge?\n"
        "Context: schema migration in PR.\n"
        "Options: **yes-apply-on-merge** / **hold-for-staging-first** / **revert-from-PR**.\n"
    )
    items, mode = parse_action_items_from_markdown(body)
    assert mode == "single_choice"
    assert [it["id"] for it in items] == [
        "q1-yes-apply-on-merge",
        "q1-hold-for-staging-first",
        "q1-revert-from-pr",
    ]
    assert [it["kind"] for it in items] == ["choice", "choice", "choice"]
    assert items[0]["label"] == "yes-apply-on-merge"


def test_q1_plus_q2_returns_multi_select() -> None:
    body = (
        "**Q1.** Merge order?\n"
        "Options: **155-first** / **156-first**.\n\n"
        "**Q2.** CI failure on unrelated tests?\n"
        "Options: **fix-on-branch** / **wait-for-main**.\n"
    )
    items, mode = parse_action_items_from_markdown(body)
    assert mode == "multi_select"
    ids = [it["id"] for it in items]
    assert ids == [
        "q1-155-first",
        "q1-156-first",
        "q2-fix-on-branch",
        "q2-wait-for-main",
    ]


def test_options_with_uppercase_letters_slugified() -> None:
    body = (
        "**Q1.** Choose mode\n"
        "Options: **Yes-Apply** / **Hold-For-Review**.\n"
    )
    items, _ = parse_action_items_from_markdown(body)
    assert items[0]["id"] == "q1-yes-apply"
    assert items[0]["label"] == "Yes-Apply"


def test_options_line_missing_returns_no_items() -> None:
    body = "**Q1.** Just a question without Options line.\nContext: blah.\n"
    items, mode = parse_action_items_from_markdown(body)
    assert items == []
    assert mode == "freeform_only"
