# Backend tests

Run from the repo root so `backend.app.main` resolves on `sys.path`:

```bash
PYTHONPATH=. pytest backend/tests -v
```

Tests cover:

- Manifest validation and `/manifest` aggregate endpoint.
- Catalog list endpoints (`/patterns`, `/tools`, ...) include the new version fields.
- `?version=` pinning on catalog `get` and `POST /fetch`.
- `/feedback` with artifact payload + dedup comment path (GitHub is mocked via `httpx.MockTransport`).
- `/telemetry` batch accept, UUIDv4 validation, denylist, rate limit, delete + export.
- `scripts/ship_artifact_check.py` drift detection.
