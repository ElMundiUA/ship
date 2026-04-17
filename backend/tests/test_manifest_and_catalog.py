from __future__ import annotations


def test_manifest_endpoint(client):
    resp = client.get("/manifest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert "generated_at" in body
    assert isinstance(body["entries"], list)
    assert body["entries"], "expected at least one entry"

    kinds = {e["kind"] for e in body["entries"]}
    # Should cover every catalog kind plus docs.
    assert {"pattern", "tool", "workflow", "collection", "doc"}.issubset(kinds)

    required_fields = {
        "kind",
        "id",
        "version",
        "content_sha256",
        "updated_at",
        "channel",
        "deprecated",
        "yanked",
        "path",
    }
    for entry in body["entries"]:
        assert required_fields.issubset(entry.keys()), entry


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
