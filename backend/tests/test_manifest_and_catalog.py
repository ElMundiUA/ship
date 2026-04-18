from __future__ import annotations


def test_patterns_list_includes_version(client):
    resp = client.get("/patterns")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("patterns"), list)
    assert data["patterns"], "expected patterns manifest to be non-empty"
    first = data["patterns"][0]
    for field in (
        "id",
        "version",
        "content_sha256",
        "updated_at",
        "channel",
        "deprecated",
        "replaced_by",
        "yanked",
    ):
        assert field in first, f"missing {field} in /patterns entry"


def test_pattern_get_with_version_match(client):
    resp = client.get("/patterns")
    pattern = resp.json()["patterns"][0]
    pid = pattern["id"]
    current = pattern["version"]

    ok = client.get(f"/patterns/{pid}", params={"version": current})
    assert ok.status_code == 200
    body = ok.json()
    assert body["id"] == pid
    assert body["version"] == current
    assert body["kind"] == "pattern"
    assert "content" in body


def test_pattern_get_with_version_mismatch(client):
    resp = client.get("/patterns")
    pid = resp.json()["patterns"][0]["id"]

    mis = client.get(f"/patterns/{pid}", params={"version": "9.9.9"})
    assert mis.status_code == 404
    detail = mis.json()["detail"]
    assert "unknown version" in detail
    assert "current is" in detail


def test_versions_endpoint_returns_single_entry(client):
    resp = client.get("/patterns")
    pid = resp.json()["patterns"][0]["id"]

    v = client.get(f"/patterns/{pid}/versions")
    assert v.status_code == 200
    body = v.json()
    assert body["id"] == pid
    assert len(body["versions"]) == 1
    entry = body["versions"][0]
    assert {"version", "updated_at", "channel", "deprecated", "yanked"}.issubset(entry.keys())


def test_fetch_with_version_mismatch(client):
    resp = client.get("/patterns")
    pid = resp.json()["patterns"][0]["id"]

    bad = client.post(
        "/fetch",
        json={"kind": "pattern", "id": pid, "version": "0.0.1"},
    )
    assert bad.status_code == 404
    assert "unknown version" in bad.json()["detail"]


def test_patterns_list_uses_artifact_folder(client):
    resp = client.get("/patterns")
    assert resp.status_code == 200
    data = resp.json()
    first = data["patterns"][0]
    assert first["path"].startswith("artifacts/patterns/")
    assert first["path"].endswith("/ARTIFACT.md")


def test_pattern_get_returns_full_artifact_md(client):
    resp = client.get("/patterns")
    pid = resp.json()["patterns"][0]["id"]
    body = client.get(f"/patterns/{pid}").json()
    assert body["content"].startswith("---\n")
