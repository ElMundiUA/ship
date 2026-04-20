"""Unit tests for :mod:`backend.app.services.agent.kb_indexer`.

The indexer is the one piece of agent plumbing whose correctness is
easiest to verify in isolation: the chunker is a pure function, and
the :meth:`reindex_repo_kb` driver can be exercised against a fake
:class:`CodeHostGateway` + a real Postgres session.

These tests cover the deterministic bits (chunker) without a DB;
the full driver is tested in ``test_v1_kb_reindex`` where the
reindex endpoint gives us an authenticated request surface.
"""

from __future__ import annotations

from backend.app.services.agent.kb_indexer import (
    _chunk_markdown,
    _split_long_paragraph,
)


def test_chunker_returns_empty_for_blank_input() -> None:
    assert _chunk_markdown("") == []
    assert _chunk_markdown("   \n   \n") == []


def test_chunker_folds_heading_into_following_paragraph() -> None:
    text = "# Intro\n\nThis is the intro body."
    chunks = _chunk_markdown(text)
    assert len(chunks) == 1
    assert chunks[0].startswith("# Intro")
    assert "intro body" in chunks[0]


def test_chunker_respects_target_size() -> None:
    # Five 200-char paragraphs: each chunk should group several of
    # them together but stay near the 800-char target.
    paragraph = "x" * 200
    text = "\n\n".join([paragraph] * 5)
    chunks = _chunk_markdown(text)
    assert chunks, "expected at least one chunk"
    # We don't check exact count — we just guarantee no chunk goes
    # past the hard cap and chunks aren't trivially huge either.
    for chunk in chunks:
        assert len(chunk) <= 1600


def test_chunker_splits_oversize_paragraph_on_sentences() -> None:
    # 40 substantial sentences overflow the ~800-char target even
    # though each fits well under it. The split helper should
    # coalesce them into multiple chunks rather than one mega-chunk.
    huge = " ".join(f"Sentence {i} " + "x" * 40 + "." for i in range(40))
    pieces = _split_long_paragraph(huge)
    assert len(pieces) >= 2
    for piece in pieces:
        assert len(piece) <= 1600


def test_chunker_standalone_heading_survives() -> None:
    """A trailing heading with no body should still appear as a chunk."""
    text = "Body content here.\n\n## Section"
    chunks = _chunk_markdown(text)
    # Either grouped together or the heading stands alone — in
    # either case the section title shouldn't disappear.
    assert any("## Section" in c for c in chunks)
