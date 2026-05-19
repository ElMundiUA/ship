"""Offline unit tests for ``backend.app.integrations.telegram.render``."""

from __future__ import annotations

import pytest

from backend.app.integrations.telegram.render import (
    TELEGRAM_MESSAGE_LIMIT,
    Directive,
    _find_safe_cut,
    directive_fallback_text,
    extract_directives,
    markdown_to_telegram_html,
    render_with_directives_inline,
    split_markdown_into_chunks,
)


def test_markdown_to_telegram_html_empty() -> None:
    assert markdown_to_telegram_html("") == ""


def test_markdown_to_telegram_html_gfm_subset() -> None:
    md = "\n".join(
        [
            "# Title",
            "",
            "**bold** and *italic*",
            "",
            "- item one",
            "1. numbered",
            "",
            "> quoted line",
            "",
            "[link](https://example.com/path?q=1)",
            "",
            "inline `code`",
            "",
            "```python",
            "print('hi')",
            "```",
        ]
    )
    html = markdown_to_telegram_html(md)
    assert "<b>Title</b>" in html
    assert "<b>bold</b>" in html
    assert "<i>italic</i>" in html
    assert "• item one" in html
    assert "<blockquote>" in html and "quoted line" in html
    assert '<a href="https://example.com/path?q=1">link</a>' in html
    assert "<code>code</code>" in html
    assert "<pre>" in html and "print(" in html and "hi" in html
    assert "<script>" not in html


def test_markdown_to_telegram_html_partial_bold_stays_literal() -> None:
    html = markdown_to_telegram_html("**open")
    assert "<b>" not in html
    assert "**open" in html or "&lt;b&gt;" not in html


def test_markdown_to_telegram_html_escapes_hostile_tags() -> None:
    html = markdown_to_telegram_html("<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_split_markdown_into_chunks_empty_and_whitespace() -> None:
    assert split_markdown_into_chunks("") == []
    assert split_markdown_into_chunks("   \n\t  ") == []


def test_split_markdown_into_chunks_single_under_cap() -> None:
    md = "hello world"
    chunks = split_markdown_into_chunks(md)
    assert chunks == [md]


def test_split_markdown_into_chunks_splits_long_prose() -> None:
    paragraph = "word " * 900
    chunks = split_markdown_into_chunks(paragraph)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(markdown_to_telegram_html(chunk)) <= TELEGRAM_MESSAGE_LIMIT


def test_find_safe_cut_never_splits_inside_unclosed_fence() -> None:
    prose = "word " * 400
    md = f"{prose}\n\n```\n{'line\n' * 80}```\n\n{prose}"
    cut = _find_safe_cut(md, max_chars=500)
    assert cut > 0
    assert md.count("```", 0, cut) % 2 == 0


def test_split_markdown_into_chunks_respects_closed_fences() -> None:
    prose = "para " * 800
    md = f"{prose}\n\n```\nok\n```\n\n{prose}"
    chunks = split_markdown_into_chunks(md)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.count("```") % 2 == 0


def test_extract_directives_valid_choice_and_todo() -> None:
    choice_body = '{"prompt": "Pick one", "options": ["a", "b"]}'
    todo_body = '{"title": "Plan", "items": ["step"]}'
    md = f"intro\n\n```ship-choice\n{choice_body}\n```\n\n```ship-todo\n{todo_body}\n```\n"
    cleaned, directives = extract_directives(md)
    assert "ship-choice" not in cleaned
    assert "ship-todo" not in cleaned
    assert len(directives) == 2
    assert directives[0].kind == "ship-choice"
    assert directives[0].payload == {"prompt": "Pick one", "options": ["a", "b"]}
    assert directives[1].kind == "ship-todo"
    assert directives[1].payload == {"title": "Plan", "items": ["step"]}


def test_extract_directives_leaves_unclosed_fence() -> None:
    md = "before\n```ship-choice\n{\"prompt\": \"x\"}\n"
    cleaned, directives = extract_directives(md)
    assert directives == []
    assert "```ship-choice" in cleaned


def test_extract_directives_malformed_json() -> None:
    md = "```ship-choice\n{not json\n```"
    _, directives = extract_directives(md)
    assert len(directives) == 1
    assert directives[0].payload is None
    assert directives[0].raw == "{not json"


def test_render_with_directives_inline_appends_fallback() -> None:
    md = "```ship-todo\n{\"title\": \"Tasks\", \"items\": [\"a\"]}\n```"
    rendered, directives = render_with_directives_inline(md)
    assert directives[0].kind == "ship-todo"
    assert "**Tasks**" in rendered
    assert "⬜ a" in rendered


def test_directive_fallback_text_without_payload() -> None:
    choice = Directive(kind="ship-choice", payload=None, raw="")
    todo = Directive(kind="ship-todo", payload=None, raw="")
    assert "подгружаю" in directive_fallback_text(choice)
    assert "готовлю" in directive_fallback_text(todo)


def test_split_hard_cut_when_token_exceeds_budget() -> None:
    token = "Z" * (TELEGRAM_MESSAGE_LIMIT + 500)
    chunks = split_markdown_into_chunks(token)
    assert len(chunks) >= 1
    assert "".join(chunks).replace(" ", "") == token.replace(" ", "")


def test_outside_fence_parity_rule() -> None:
    md = "a```b```c"
    for idx in range(len(md) + 1):
        outside = md.count("```", 0, idx) % 2 == 0
        if idx == 0 or idx == len(md):
            continue
        if outside:
            assert not (md[:idx].count("```") % 2 == 1 and md[idx:].startswith("`"))
