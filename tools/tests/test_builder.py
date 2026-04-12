from pathlib import Path

from conftest import DummyEmbeddingFunction


def test_build_index_creates_docs_index(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    chroma_path = tmp_path / "chroma_db"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "doc1.md").write_text("# Title\n\nGetting started guide.", encoding="utf-8")

    from src import builder

    monkeypatch.setattr(builder, "get_embedding_function", lambda: DummyEmbeddingFunction())
    builder.build_index(Path(data_dir), Path(chroma_path))

    docs_index = chroma_path / "docs_index.json"
    assert docs_index.exists()
    text = docs_index.read_text(encoding="utf-8")
    assert "doc1" in text
