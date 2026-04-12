import os
from typing import Iterable, List, Sequence

import httpx


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y")


def _chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class MistralEmbeddingFunction:
    """Embedding function that calls a Mistral-compatible HTTP API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "mistral-embed",
        embeddings_path: str = "/v1/embeddings",
        embeddings_url: str | None = None,
        timeout_s: float = 60.0,
        batch_size: int = 32,
        verify_ssl: bool = True,
        debug: bool = False,
    ) -> None:
        if not base_url and not embeddings_url:
            raise ValueError("base_url or embeddings_url must be provided")
        if not api_key:
            raise ValueError("api_key is required for Mistral embeddings")

        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.batch_size = max(1, batch_size)
        self.verify_ssl = verify_ssl
        self.debug = debug

        if embeddings_url:
            self.endpoint = embeddings_url
        else:
            # If base URL already looks like a full endpoint or has query params, use as-is.
            if ("embeddings" in base_url) or ("?" in base_url):
                self.endpoint = base_url
            else:
                self.endpoint = base_url.rstrip("/") + embeddings_path

    def name(self) -> str:
        return "mistral-embeddings"

    def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        vectors: List[List[float]] = []
        with httpx.Client(timeout=self.timeout_s, verify=self.verify_ssl) as client:
            for batch in _chunked(texts, self.batch_size):
                payload = {"model": self.model, "input": batch}
                resp = client.post(self.endpoint, headers=headers, json=payload)
                if resp.status_code >= 400:
                    if self.debug:
                        raise RuntimeError(
                            f"Embeddings request failed: {resp.status_code} {resp.text}"
                        )
                    resp.raise_for_status()
                data = resp.json().get("data", [])
                # Preserve ordering
                batch_vectors = [item["embedding"] for item in data]
                vectors.extend(batch_vectors)

        return vectors

    @staticmethod
    def _normalize_texts(input: object) -> List[str]:
        if isinstance(input, list):
            # Chroma may pass a list of strings
            return [str(x) for x in input]
        return [str(input)]

    def __call__(self, input: List[str]) -> List[List[float]]:
        return self._embed_texts(self._normalize_texts(input))

    def embed_query(self, input: object) -> List[List[float]]:
        texts = self._normalize_texts(input)
        return [self._embed_texts([texts[0]])[0]]

    def embed_documents(self, input: object) -> List[List[float]]:
        return self._embed_texts(self._normalize_texts(input))


def get_embedding_function():
    """Select embedding function based on env vars."""
    base_url = os.environ.get("MISTRAL_BASE_URL", "").strip() or os.environ.get("EMBED_BASE_URL", "").strip()
    api_key = os.environ.get("MISTRAL_API_KEY", "").strip() or os.environ.get("EMBED_API_KEY", "").strip()
    embeddings_url = os.environ.get("MISTRAL_EMBEDDINGS_URL", "").strip()

    if api_key and (base_url or embeddings_url):
        embeddings_path = os.environ.get("MISTRAL_EMBEDDINGS_PATH", "/v1/embeddings").strip() or "/v1/embeddings"
        if base_url.endswith("/v1") and embeddings_path == "/v1/embeddings":
            embeddings_path = "/embeddings"
        return MistralEmbeddingFunction(
            base_url=base_url,
            api_key=api_key,
            model=(
                os.environ.get("MISTRAL_MODEL", "").strip()
                or os.environ.get("EMBED_MODEL", "").strip()
                or "mistral-embed"
            ),
            embeddings_path=embeddings_path,
            embeddings_url=embeddings_url or None,
            timeout_s=float(os.environ.get("MISTRAL_TIMEOUT_S", "60")),
            batch_size=int(os.environ.get("MISTRAL_BATCH_SIZE", "32")),
            verify_ssl=_env_bool("MISTRAL_SSL_VERIFY", True),
            debug=_env_bool("MISTRAL_DEBUG_LOG", False),
        )

    # Fallback to local sentence-transformers
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError:
        from chromadb.utils.embedding_functions.sentence_transformer_embedding_function import (
            SentenceTransformerEmbeddingFunction,
        )

    return SentenceTransformerEmbeddingFunction(
        model_name=os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
        device="cpu",
        normalize_embeddings=True,
    )
