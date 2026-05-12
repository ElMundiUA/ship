"""One-shot: dump a Linear issue's title, state, body, and recent
comments to stdout. Used to read tickets without opening the browser
when the agent is the one driving the PR work.

Usage:

    python -m scripts.peek_linear_issue ELS-72
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import safe_decrypt


SHIP_ON_SHIP_WS = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
ELS_TEAM_ID = "854ffe38-2ac7-404f-b482-7260ac707593"


async def main(identifier: str) -> int:
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 2
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parts = urlsplit(db_url)
    qs = dict(parse_qsl(parts.query))
    sslmode = qs.pop("sslmode", None)
    qs.pop("channel_binding", None)
    db_url = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment)
    )
    connect_args: dict = {}
    if sslmode and sslmode != "disable":
        connect_args["ssl"] = True

    engine = create_async_engine(db_url, future=True, connect_args=connect_args)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        row = (
            await session.execute(
                select(Integration)
                .where(
                    Integration.workspace_id == SHIP_ON_SHIP_WS,
                    Integration.kind == "linear",
                )
                .order_by(Integration.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            print("ERROR: no Linear Integration row", file=sys.stderr)
            return 3
        token = safe_decrypt(row.secret_ciphertext)
        if not token:
            return 4

    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": token},
            json={
                "query": """
                query Peek($id: String!) {
                  issue(id: $id) {
                    id
                    identifier
                    title
                    description
                    url
                    state { name type }
                    project { id name slugId }
                    labels { nodes { name } }
                    comments(first: 30, orderBy: createdAt) {
                      nodes {
                        id
                        body
                        createdAt
                        user { name email }
                      }
                    }
                    updatedAt
                  }
                }
                """,
                "variables": {"id": identifier},
            },
        )
        body = resp.json()

    issue = (body.get("data") or {}).get("issue")
    if not issue:
        print(f"NOT FOUND: {identifier}", file=sys.stderr)
        if body.get("errors"):
            print(f"errors: {body['errors']}", file=sys.stderr)
        return 5

    print(f"# {issue['identifier']}: {issue['title']}")
    print()
    print(f"- url: {issue['url']}")
    print(f"- state: {issue['state']['name']} ({issue['state']['type']})")
    proj = issue.get("project") or {}
    if proj:
        print(f"- project: {proj.get('name')!r} ({proj.get('id')})")
    label_names = [
        ln["name"] for ln in (issue.get("labels") or {}).get("nodes", [])
    ]
    if label_names:
        print(f"- labels: {', '.join(label_names)}")
    print(f"- updated_at: {issue.get('updatedAt')}")
    print()
    print("## Description")
    print()
    print(issue.get("description") or "(no description)")
    print()
    comments = (issue.get("comments") or {}).get("nodes") or []
    if comments:
        print(f"## Comments ({len(comments)})")
        print()
        for c in comments:
            user = (c.get("user") or {}).get("name") or "?"
            print(f"### {user} — {c.get('createdAt')}")
            print()
            print(c.get("body") or "")
            print()

    await engine.dispose()
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "ELS-72"
    sys.exit(asyncio.run(main(arg)))
