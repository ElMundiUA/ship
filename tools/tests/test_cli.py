import sys

from conftest import build_test_index


def test_cli_search_and_fetch(tmp_path, monkeypatch, capsys):
    chroma_path = build_test_index(tmp_path, monkeypatch)

    from src import cli

    # Force CLI to use dummy embeddings for compatibility in tests
    from conftest import DummyEmbeddingFunction

    monkeypatch.setattr(cli, "get_embedding_function", lambda: DummyEmbeddingFunction())
    # Search
    sys.argv = ["docs-mcp-cli", "search", "getting started", "--top-k", "2", "--chroma-path", str(chroma_path)]
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "doc1" in out

    # Fetch
    sys.argv = ["docs-mcp-cli", "fetch", "doc1", "--chroma-path", str(chroma_path)]
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "Getting started" in out
