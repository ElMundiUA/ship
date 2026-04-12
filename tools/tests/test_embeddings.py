from src.embeddings import MistralEmbeddingFunction


def test_mistral_embedding_function_interface():
    f = MistralEmbeddingFunction(
        base_url="https://example.com",
        api_key="x",
        verify_ssl=False,
    )
    assert callable(f.name)
    assert callable(f.embed_query)
    assert callable(f.embed_documents)
    assert "embeddings" in f.name()


def test_mistral_normalizes_input_shapes(monkeypatch):
    f = MistralEmbeddingFunction(
        base_url="https://example.com",
        api_key="x",
        verify_ssl=False,
    )

    captured = {}

    def _fake_embed(texts):
        captured["texts"] = list(texts)
        return [[1.0, 2.0, 3.0] for _ in texts]

    monkeypatch.setattr(f, "_embed_texts", _fake_embed)

    # Query path should pass a single string, not nested list
    f.embed_query(["hello"])
    assert captured["texts"] == ["hello"]

    # Documents path should accept list of strings
    f.embed_documents(["a", "b"])
    assert captured["texts"] == ["a", "b"]
