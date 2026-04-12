from conftest import build_test_index, reload_server_with_env


def test_server_search_and_fetch(tmp_path, monkeypatch):
    chroma_path = build_test_index(tmp_path, monkeypatch)
    server = reload_server_with_env(monkeypatch, chroma_path)

    search_res = server.search("getting started", top_k=3)
    assert "doc1" in search_res

    fetch_res = server.fetch("doc1")
    assert "Getting started" in fetch_res
