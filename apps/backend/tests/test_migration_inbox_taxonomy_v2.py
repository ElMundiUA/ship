"""Migration tests for ``0074_inbox_taxonomy_v2`` (ELS-143).

Exercises upgrade → downgrade → upgrade on an isolated downgrade to
``0073_local_memory_adapters``, with five fixture inbox rows covering
category mapping, auto-recovered blockers, and stale dismiss.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

# Alembic downgrade/upgrade must not overlap other tests touching the DB.
_MIGRATION_LOCK = threading.Lock()

pytestmark = pytest.mark.slow
from sqlalchemy import text

from backend.tests.db_conftest import DEFAULT_TEST_DATABASE_URL, _sync_url


def _psycopg_url(url: str | None = None) -> str:
    """Normalize async/SQLAlchemy URLs for ``psycopg.connect``."""
    raw = url or os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    sync = _sync_url(raw)
    if sync.startswith("postgresql+psycopg://"):
        return "postgresql://" + sync[len("postgresql+psycopg://") :]
    return sync


_REVISION = "0074_inbox_taxonomy_v2"
_DOWNGRADE_TO = "0073_local_memory_adapters"


def _alembic_config():
    from alembic.config import Config

    repo_root = os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    )
    cfg = Config(os.path.join(repo_root, "apps", "backend", "alembic.ini"))
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    cfg.set_main_option("sqlalchemy.url", _sync_url(url))
    cfg.set_main_option(
        "script_location",
        os.path.join(repo_root, "apps", "backend", "migrations"),
    )
    return cfg


def _require_postgres():
    import psycopg

    url = _psycopg_url()
    try:
        with psycopg.connect(url) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres unreachable ({exc!s})")


@pytest.fixture
def taxonomy_db():
    """Downgrade to pre-taxonomy head, yield URL, restore head after test."""
    from alembic import command

    _require_postgres()
    with _MIGRATION_LOCK:
        cfg = _alembic_config()
        command.downgrade(cfg, _DOWNGRADE_TO)
        url = _psycopg_url()
        try:
            yield url
        finally:
            command.upgrade(cfg, "head")


@contextmanager
def _connect(url: str):
    """Short-lived autocommit connection so Alembic never blocks on idle xacts."""
    import psycopg

    conn = psycopg.connect(url)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _insert_workspace(conn) -> uuid.UUID:
    ws_id = uuid.uuid4()
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (id, email, display_name)
            VALUES (%s, %s, 'taxonomy-test')
            """,
            (user_id, f"taxonomy-{ws_id.hex[:8]}@example.com"),
        )
        cur.execute(
            """
            INSERT INTO orgs (id, slug, name, plan)
            VALUES (%s, %s, 'Taxonomy Org', 'free')
            """,
            (org_id, f"org-{ws_id.hex[:8]}"),
        )
        cur.execute(
            """
            INSERT INTO org_members (org_id, user_id, role)
            VALUES (%s, %s, 'org_owner')
            """,
            (org_id, user_id),
        )
        cur.execute(
            """
            INSERT INTO workspaces (id, org_id, slug, name)
            VALUES (%s, %s, %s, 'Taxonomy WS')
            """,
            (ws_id, org_id, f"ws-{ws_id.hex[:6]}"),
        )
        cur.execute(
            """
            INSERT INTO workspace_members (
                workspace_id, user_id, role, answer_specialist_slugs
            )
            VALUES (%s, %s, 'owner', '[]'::jsonb)
            """,
            (ws_id, user_id),
        )
    return ws_id


def _seed_fixtures(conn, workspace_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Five inbox rows + one recovery audit for the auto-recovered blocker."""
    now = datetime.now(timezone.utc)
    ids = {
        "clarification": uuid.uuid4(),
        "approval": uuid.uuid4(),
        "blocker_recovered": uuid.uuid4(),
        "blocker_stale": uuid.uuid4(),
        "report": uuid.uuid4(),
    }
    ticket_ref = "ELS-TEST-1"
    fsm_stage = "dev_implementation"
    recovery_at = now - timedelta(hours=1)
    stale_created = now - timedelta(hours=48)

    rows = [
        (
            ids["clarification"],
            "clarification",
            "new",
            {},
            now,
        ),
        (
            ids["approval"],
            "approval",
            "new",
            {},
            now,
        ),
        (
            ids["blocker_recovered"],
            "blocker",
            "new",
            {"ticket_ref": ticket_ref, "fsm_stage": fsm_stage},
            now - timedelta(hours=6),
        ),
        (
            ids["blocker_stale"],
            "blocker",
            "new",
            {"ticket_ref": "ELS-STALE", "fsm_stage": fsm_stage},
            stale_created,
        ),
        (
            ids["report"],
            "report",
            "new",
            {},
            now,
        ),
    ]

    with conn.cursor() as cur:
        for item_id, itype, status, payload, created_at in rows:
            intake_reason = (
                "agent_run_blocked" if itype == "blocker" else None
            )
            cur.execute(
                """
                INSERT INTO inbox_items (
                    id, workspace_id, type, title, payload, status,
                    intake_reason, created_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                """,
                (
                    item_id,
                    workspace_id,
                    itype,
                    f"Fixture {itype}",
                    json.dumps(payload),
                    status,
                    intake_reason,
                    created_at,
                ),
            )

        cur.execute(
            """
            INSERT INTO audit_log (
                workspace_id, action, target_id, payload, created_at
            )
            VALUES (%s, 'agent_run.finish', %s, %s::jsonb, %s)
            """,
            (
                workspace_id,
                ticket_ref,
                json.dumps(
                    {
                        "outcome": "ready_next_step",
                        "ticket_ref": ticket_ref,
                        "fsm_stage": fsm_stage,
                    }
                ),
                recovery_at,
            ),
        )
    return ids


def _upgrade_taxonomy() -> None:
    from alembic import command

    command.upgrade(_alembic_config(), _REVISION)


def _row(conn, item_id: uuid.UUID) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT type, status, resolution, category, priority,
                   auto_resolvable, headline
              FROM inbox_items
             WHERE id = %s
            """,
            (item_id,),
        )
        row = cur.fetchone()
    assert row is not None
    keys = (
        "type",
        "status",
        "resolution",
        "category",
        "priority",
        "auto_resolvable",
        "headline",
    )
    return dict(zip(keys, row, strict=True))


def test_inbox_taxonomy_v2_upgrade_mapping_and_backfill(taxonomy_db) -> None:
    with _connect(taxonomy_db) as conn:
        ws_id = _insert_workspace(conn)
        ids = _seed_fixtures(conn, ws_id)

    _upgrade_taxonomy()

    with _connect(taxonomy_db) as conn:

        assert _row(conn, ids["clarification"]) == {
            "type": "clarification",
            "status": "new",
            "resolution": None,
            "category": "decision_needed",
            "priority": 20,
            "auto_resolvable": False,
            "headline": "Fixture clarification"[:80],
        }
        assert _row(conn, ids["approval"])["category"] == "decision_needed"
        assert _row(conn, ids["report"])["category"] == "dismiss_silently"

        recovered = _row(conn, ids["blocker_recovered"])
        assert recovered["status"] == "resolved"
        assert recovered["resolution"] == "auto_recovered"
        assert recovered["category"] == "failure"
        assert recovered["auto_resolvable"] is True

        stale = _row(conn, ids["blocker_stale"])
        assert stale["status"] == "dismissed"
        assert stale["resolution"] == "stale"

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'inbox_items' AND column_name = 'category'
                """
            )
            assert cur.fetchone() is not None
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'run_escalations'
                   AND column_name = 'resolved_at'
                """
            )
            assert cur.fetchone() is not None


def test_inbox_taxonomy_v2_round_trip(taxonomy_db) -> None:
    from alembic import command

    with _connect(taxonomy_db) as conn:
        ws_id = _insert_workspace(conn)
        ids = _seed_fixtures(conn, ws_id)

    cfg = _alembic_config()
    command.upgrade(cfg, _REVISION)
    with _connect(taxonomy_db) as conn:
        before = _row(conn, ids["clarification"])

    command.downgrade(cfg, _DOWNGRADE_TO)
    with _connect(taxonomy_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'inbox_items' AND column_name = 'category'
                """
            )
            assert cur.fetchone() is None

    command.upgrade(cfg, _REVISION)
    with _connect(taxonomy_db) as conn:
        after = _row(conn, ids["clarification"])
    assert after["category"] == before["category"]


def test_inbox_taxonomy_v2_idempotent_reupgrade(taxonomy_db) -> None:
    from alembic import command

    with _connect(taxonomy_db) as conn:
        ws_id = _insert_workspace(conn)
        ids = _seed_fixtures(conn, ws_id)

    cfg = _alembic_config()
    command.upgrade(cfg, _REVISION)
    with _connect(taxonomy_db) as conn:
        snapshot = {k: _row(conn, v) for k, v in ids.items()}
    command.upgrade(cfg, _REVISION)
    with _connect(taxonomy_db) as conn:
        for key, item_id in ids.items():
            assert _row(conn, item_id) == snapshot[key]


def test_inbox_taxonomy_v2_wrong_fsm_stage_not_auto_resolved(taxonomy_db) -> None:
    """Recovery audit with mismatched fsm_stage must not resolve the blocker."""
    now = datetime.now(timezone.utc)
    ticket_ref = "ELS-TEST-WRONG-FSM"
    item_id = uuid.uuid4()

    with _connect(taxonomy_db) as conn:
        ws_id = _insert_workspace(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO inbox_items (
                    id, workspace_id, type, title, payload, status,
                    intake_reason, created_at
                )
                VALUES (%s, %s, 'blocker', 'Wrong FSM', %s::jsonb, 'new',
                        'agent_run_blocked', %s)
                """,
                (
                    item_id,
                    ws_id,
                    json.dumps(
                        {
                            "ticket_ref": ticket_ref,
                            "fsm_stage": "dev_implementation",
                        }
                    ),
                    now - timedelta(hours=6),
                ),
            )
            cur.execute(
                """
                INSERT INTO audit_log (
                    workspace_id, action, target_id, payload, created_at
                )
                VALUES (%s, 'agent_run.finish', %s, %s::jsonb, %s)
                """,
                (
                    ws_id,
                    ticket_ref,
                    json.dumps(
                        {
                            "outcome": "ready_next_step",
                            "ticket_ref": ticket_ref,
                            "fsm_stage": "qa_manual",
                        }
                    ),
                    now - timedelta(hours=1),
                ),
            )

    _upgrade_taxonomy()

    with _connect(taxonomy_db) as conn:
        row = _row(conn, item_id)
    assert row["status"] == "new"
    assert row["resolution"] is None


def test_inbox_taxonomy_v2_preserves_human_terminal_resolution(taxonomy_db) -> None:
    """Pre-resolved rows keep operator resolution through upgrade."""
    now = datetime.now(timezone.utc)
    user_id = uuid.uuid4()
    item_id = uuid.uuid4()

    with _connect(taxonomy_db) as conn:
        ws_id = _insert_workspace(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, email, display_name)
                VALUES (%s, %s, 'terminal-test')
                """,
                (user_id, f"terminal-{item_id.hex[:8]}@example.com"),
            )
            cur.execute(
                """
                INSERT INTO inbox_items (
                    id, workspace_id, type, title, payload, status,
                    resolution, resolved_at, resolved_by_user_id, created_at
                )
                VALUES (%s, %s, 'blocker', 'Human resolved', '{}'::jsonb,
                        'resolved', 'answered', %s, %s, %s)
                """,
                (item_id, ws_id, now, user_id, now),
            )

    _upgrade_taxonomy()

    with _connect(taxonomy_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, resolution, resolved_by_user_id
                  FROM inbox_items
                 WHERE id = %s
                """,
                (item_id,),
            )
            status, resolution, resolved_by = cur.fetchone()
    assert status == "resolved"
    assert resolution == "answered"
    assert resolved_by == user_id


def test_inbox_taxonomy_v2_category_check_rejects_invalid(taxonomy_db) -> None:
    """CHECK on category rejects values outside the v2 enum."""
    import psycopg

    with _connect(taxonomy_db) as conn:
        ws_id = _insert_workspace(conn)
        _seed_fixtures(conn, ws_id)

    _upgrade_taxonomy()

    with _connect(taxonomy_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM inbox_items
                 WHERE workspace_id = %s
                 LIMIT 1
                """,
                (ws_id,),
            )
            (item_id,) = cur.fetchone()
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE inbox_items
                       SET category = 'not_a_category'
                     WHERE id = %s
                    """,
                    (item_id,),
                )


@pytest.mark.asyncio
async def test_migration_head_includes_taxonomy_columns(db_session, _migrated) -> None:
    """Sanity: session-scoped head migration exposes new columns."""
    rows = (
        await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'inbox_items' AND column_name IN "
                "('category', 'priority', 'auto_resolvable', 'headline') "
                "ORDER BY column_name"
            )
        )
    ).all()
    assert [r[0] for r in rows] == [
        "auto_resolvable",
        "category",
        "headline",
        "priority",
    ]
