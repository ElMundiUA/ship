import argparse
import json
import os
from pathlib import Path

import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv

from src.builder import build_index
from src.embeddings import get_embedding_function


def _get_collection(chroma_path: Path):
    ef = get_embedding_function()
    client = chromadb.PersistentClient(
        path=str(chroma_path),
        settings=Settings(anonymized_telemetry=False),
    )
    return client.get_collection(name="docs", embedding_function=ef)


def cmd_build(args: argparse.Namespace) -> int:
    build_index(Path(args.data_dir), Path(args.chroma_path))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    coll = _get_collection(Path(args.chroma_path))
    results = coll.query(query_texts=[args.query], n_results=min(args.top_k, 20))
    if not results or not results["documents"] or not results["documents"][0]:
        print("No results found.")
        return 0

    docs = results["documents"][0]
    metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
    dists = results["distances"][0] if results.get("distances") else [0] * len(docs)

    lines = []
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists), 1):
        doc_id = meta.get("doc_id", "unknown")
        lines.append(f"--- Result {i} (doc: {doc_id}, score: {1 - dist / 2:.2f}) ---\n{doc}")
    print("\n\n".join(lines))
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    idx_path = Path(args.chroma_path) / "docs_index.json"
    if not idx_path.exists():
        print("docs_index.json not found. Build the index first.")
        return 1
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    if args.doc_id not in index:
        print(f"Document '{args.doc_id}' not found.")
        return 1
    print(index[args.doc_id])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docs-mcp-cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="Build Chroma index from markdown files")
    p_build.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "./data"))
    p_build.add_argument("--chroma-path", default=os.environ.get("CHROMA_PATH", "./chroma_db"))
    p_build.set_defaults(func=cmd_build)

    p_search = sub.add_parser("search", help="Search documents")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--chroma-path", default=os.environ.get("CHROMA_PATH", "./chroma_db"))
    p_search.set_defaults(func=cmd_search)

    p_fetch = sub.add_parser("fetch", help="Fetch full document by ID")
    p_fetch.add_argument("doc_id")
    p_fetch.add_argument("--chroma-path", default=os.environ.get("CHROMA_PATH", "./chroma_db"))
    p_fetch.set_defaults(func=cmd_fetch)

    return parser


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
