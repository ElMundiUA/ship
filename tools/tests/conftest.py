from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

# Ensure project root is on sys.path for `src` imports
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class DummyEmbeddingFunction:
    dim: int = 3

    def name(self) -> str:
        return "dummy-embeddings"

    def __call__(self, input: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in input:
            base = float(len(text) % 10)
            vectors.append([base, base + 1.0, base + 2.0])
        return vectors

    def embed_query(self, input: str) -> List[List[float]]:
        return [self.__call__([input])[0]]

    def embed_documents(self, input: List[str]) -> List[List[float]]:
        return self.__call__(input)


def build_test_index(tmp_path: Path, monkeypatch) -> Path:
    data_dir = tmp_path / "data"
    chroma_path = tmp_path / "chroma_db"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "doc1.md").write_text("# Title\n\nGetting started guide.", encoding="utf-8")
    (data_dir / "doc2.md").write_text("# Intro\n\nAnother document.", encoding="utf-8")

    from src import builder

    monkeypatch.setattr(builder, "get_embedding_function", lambda: DummyEmbeddingFunction())
    builder.build_index(data_dir, chroma_path)
    return chroma_path


def reload_server_with_env(monkeypatch, chroma_path: Path):
    import os
    from src import server

    monkeypatch.setenv("CHROMA_PATH", str(chroma_path))
    importlib.reload(server)
    monkeypatch.setattr(server, "get_embedding_function", lambda: DummyEmbeddingFunction())
    server._collection = None
    server._docs_index = None
    return server
