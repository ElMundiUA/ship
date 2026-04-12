"""
Build script: parse MD files, chunk, vectorize, store in ChromaDB.
Runs at Docker build time.
"""
import re
from pathlib import Path

import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv

from src.embeddings import get_embedding_function


def chunk_markdown(text: str, doc_id: str, chunk_size: int = 500, overlap: int = 50) -> list[tuple[str, dict]]:
    """Split markdown into chunks by headers and size. Returns (chunk_text, metadata)."""
    chunks: list[tuple[str, dict]] = []
    # Split by ## headers first to keep semantic units
    sections = re.split(r"\n(?=##?\s)", text.strip())
    current_chunk = []
    current_len = 0
    chunk_idx = 0

    for section in sections:
        section = section.strip()
        if not section:
            continue
        section_len = len(section)

        if current_len + section_len <= chunk_size and current_chunk:
            current_chunk.append(section)
            current_len += section_len
        else:
            if current_chunk:
                chunk_text = "\n\n".join(current_chunk)
                chunks.append(
                    (
                        chunk_text,
                        {"doc_id": doc_id, "chunk_index": chunk_idx, "source": doc_id},
                    )
                )
                chunk_idx += 1
            # Start new chunk, maybe split large section
            if section_len > chunk_size:
                words = section.split()
                current_chunk = []
                current_len = 0
                for i in range(0, len(words), chunk_size // 5):  # ~100 words per sub-chunk
                    sub = " ".join(words[i : i + chunk_size // 5])
                    if current_len + len(sub) > chunk_size and current_chunk:
                        chunk_text = "\n\n".join(current_chunk)
                        chunks.append(
                            (
                                chunk_text,
                                {"doc_id": doc_id, "chunk_index": chunk_idx, "source": doc_id},
                            )
                        )
                        chunk_idx += 1
                        current_chunk = [sub]
                        current_len = len(sub)
                    else:
                        current_chunk.append(sub)
                        current_len += len(sub)
            else:
                current_chunk = [section]
                current_len = section_len

    if current_chunk:
        chunk_text = "\n\n".join(current_chunk)
        chunks.append(
            (
                chunk_text,
                {"doc_id": doc_id, "chunk_index": chunk_idx, "source": doc_id},
            )
        )

    return chunks


def build_index(data_dir: Path, chroma_path: Path) -> None:
    """Parse MD files, chunk, embed, and store in ChromaDB."""
    data_dir = Path(data_dir)
    chroma_path = Path(chroma_path)
    chroma_path.mkdir(parents=True, exist_ok=True)

    md_files = list(data_dir.glob("**/*.md"))
    if not md_files:
        raise SystemExit(f"No .md files found in {data_dir}")

    ef = get_embedding_function()

    client = chromadb.PersistentClient(
        path=str(chroma_path),
        settings=Settings(anonymized_telemetry=False),
    )

    # Remove existing collection to rebuild from scratch
    try:
        client.delete_collection("docs")
    except Exception:
        pass

    collection = client.create_collection(
        name="docs",
        embedding_function=ef,
        metadata={"description": "Document chunks for semantic search"},
    )

    # Store full documents for fetch (doc_id -> full content)
    all_docs: dict[str, str] = {}

    for md_path in sorted(md_files):
        doc_id = md_path.stem
        content = md_path.read_text(encoding="utf-8", errors="replace")
        all_docs[doc_id] = content

        chunks = chunk_markdown(content, doc_id)
        if not chunks:
            continue

        texts = [c[0] for c in chunks]
        metadatas = [c[1] for c in chunks]
        ids = [f"{doc_id}_{i}" for i in range(len(chunks))]

        collection.add(documents=texts, metadatas=metadatas, ids=ids)

    # Persist docs index for fetch - use a simple JSON file in chroma dir
    import json
    docs_index_path = chroma_path / "docs_index.json"
    docs_index_path.write_text(json.dumps(all_docs, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Indexed {len(md_files)} documents, {collection.count()} chunks")


if __name__ == "__main__":
    import os

    load_dotenv()
    data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
    chroma_path = Path(os.environ.get("CHROMA_PATH", "/app/chroma_db"))
    build_index(data_dir, chroma_path)
