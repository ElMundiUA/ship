from __future__ import annotations

import json
import uuid


def _anon() -> str:
    return str(uuid.uuid4())


def _event(anon: str, type_: str = "artifact.fetch", payload=None):
    return {
        "type": type_,
        "anonymous_id": anon,
        "timestamp": "2026-04-17T10:00:00Z",
        "payload": payload or {
            "kind": "pattern",
            "id": "cloud-developer",
            "version": "1.0.0",
            "source": "network",
        },
    }


def test_telemetry_accept_batch(client):
    anon = _anon()
    resp = client.post(
        "/telemetry",
        json={"events": [_event(anon), _event(anon, type_="artifact.use")]},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 0

    # Follow-up export returns what we just submitted
    export = client.get(f"/telemetry/{anon}/export")
    assert export.status_code == 200
    events = export.json()["events"]
    assert len(events) == 2
    assert {e["type"] for e in events} == {"artifact.fetch", "artifact.use"}


def test_telemetry_rejects_non_uuid(client):
    resp = client.post(
        "/telemetry",
        json={"events": [_event("not-a-uuid")]},
    )
    assert resp.status_code == 400


def test_telemetry_denylist_reject(client):
    anon = _anon()
    resp = client.post(
        "/telemetry",
        json={
            "events": [
                _event(
                    anon,
                    payload={
                        "kind": "pattern",
                        "id": "cloud-developer",
                        "path": "/Users/secret/project/file.md",
                    },
                )
            ]
        },
    )
    assert resp.status_code == 400
    assert "path" in resp.json()["detail"]


def test_telemetry_denylist_nested_reject(client):
    anon = _anon()
    resp = client.post(
        "/telemetry",
        json={
            "events": [
                _event(
                    anon,
                    payload={
                        "kind": "pattern",
                        "meta": {"nested": {"email": "someone@example.com"}},
                    },
                )
            ]
        },
    )
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"]


def test_telemetry_rate_limit(client):
    anon = _anon()
    # 60 is the per-minute limit; the 61st event-batch is rejected.
    for i in range(60):
        r = client.post("/telemetry", json={"events": [_event(anon)]})
        assert r.status_code == 202, f"request {i} failed: {r.text}"
    over = client.post("/telemetry", json={"events": [_event(anon)]})
    assert over.status_code == 429


def test_telemetry_delete_requires_confirm(client):
    anon = _anon()
    client.post("/telemetry", json={"events": [_event(anon)]})

    bad = client.delete(f"/telemetry/{anon}")
    assert bad.status_code == 400

    ok = client.delete(f"/telemetry/{anon}", headers={"X-Ship-Confirm": "yes"})
    assert ok.status_code == 200
    assert ok.json()["deleted"] == 1

    after = client.get(f"/telemetry/{anon}/export")
    assert after.json()["events"] == []


def test_telemetry_rejects_unknown_type_as_rejected(client):
    anon = _anon()
    resp = client.post(
        "/telemetry",
        json={
            "events": [
                _event(anon, type_="not.a.real.type", payload={"kind": "pattern"})
            ]
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == 0
    assert body["rejected"] == 1


def test_telemetry_rejects_oversize_batch(client):
    anon = _anon()
    events = [_event(anon) for _ in range(101)]
    resp = client.post("/telemetry", json={"events": events})
    assert resp.status_code == 400
