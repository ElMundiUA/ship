"""Quick Linear fetcher for Ship-on-Ship workspace issues.

Loads the elship workspace's Linear OAuth token from the DB
``Integration`` row (NOT the .env LINEAR_API_KEY which is bound to
the elmundi org), then resolves issues by identifier (``ELS-70``)
via Linear GraphQL.

Usage:
    python scripts/linear_fetch_issue.py ELS-70 ELS-71
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / ".env"
for raw in env_path.read_text().splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.app.db.models.tenancy import Integration, Workspace  # noqa: E402
from backend.app.db.session import get_sessionmaker  # noqa: E402
from backend.app.security.encryption import decrypt  # noqa: E402

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

ISSUE_QUERY = """
query Issue($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    state { name type }
    priority
    priorityLabel
    estimate
    url
    createdAt
    updatedAt
    assignee { name email }
    creator { name email }
    project { id name }
    team { key name }
    labels { nodes { name } }
    parent { identifier title }
    comments(first: 20) {
      nodes {
        id
        createdAt
        user { name email }
        body
      }
    }
  }
}
"""


async def main(identifiers: list[str]) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        all_ws = (
            await session.execute(select(Workspace))
        ).scalars().all()
        for w in all_ws:
            print(f"ws: id={w.id} slug={w.slug} name={w.name}", file=sys.stderr)

        # Pull every Linear integration with a token; show the
        # operator the candidates so we can pick the right one.
        rows = (
            await session.execute(
                select(Integration).where(
                    Integration.kind == "linear",
                    Integration.secret_ciphertext.is_not(None),
                )
            )
        ).scalars().all()
        if not rows:
            print("No Linear integration row with a token.", file=sys.stderr)
            sys.exit(1)
        ws_by_id = {w.id: w for w in all_ws}
        for r in rows:
            w = ws_by_id.get(r.workspace_id)
            print(
                f"linear integration: id={r.id} ws_slug={w.slug if w else r.workspace_id}",
                file=sys.stderr,
            )
        # Prefer the workspace whose name/slug looks like the dogfood one.
        candidates = sorted(
            rows,
            key=lambda r: (
                "ship" not in (ws_by_id.get(r.workspace_id).slug if ws_by_id.get(r.workspace_id) else "").lower(),
                r.created_at,
            ),
        )
        candidate = candidates[0]
        w = ws_by_id.get(candidate.workspace_id)
        print(
            f"using integration: id={candidate.id} workspace={w.slug if w else candidate.workspace_id}",
            file=sys.stderr,
        )
        token = decrypt(candidate.secret_ciphertext)

    # Try every Linear integration token until one works for the ELS
    # team — we don't know up-front which workspace owns the ELS
    # Linear team OAuth.
    sessionmaker2 = get_sessionmaker()
    async with sessionmaker2() as session:
        rows2 = (
            await session.execute(
                select(Integration).where(
                    Integration.kind == "linear",
                    Integration.secret_ciphertext.is_not(None),
                )
            )
        ).scalars().all()
        all_ws2 = (await session.execute(select(Workspace))).scalars().all()
        ws_by_id2 = {w.id: w for w in all_ws2}
        tokens = []
        for r in rows2:
            try:
                tokens.append(
                    (
                        ws_by_id2.get(r.workspace_id).slug if ws_by_id2.get(r.workspace_id) else str(r.workspace_id),
                        decrypt(r.secret_ciphertext),
                    )
                )
            except Exception as exc:
                print(f"decrypt failed for {r.id}: {exc}", file=sys.stderr)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Probe each token with a viewer query first to find the
        # ELS-owning OAuth token.
        chosen: tuple[str, str] | None = None
        for slug, tok in tokens:
            probe = await client.post(
                LINEAR_GRAPHQL_URL,
                json={"query": "query { viewer { id name email } teams(first:50){nodes{key name}} }"},
                headers={
                    "Authorization": f"Bearer {tok}",
                    "Content-Type": "application/json",
                },
            )
            data = probe.json() if probe.status_code == 200 else None
            if not data or data.get("errors"):
                print(f"probe {slug}: status={probe.status_code} err={data.get('errors') if data else probe.text[:100]}", file=sys.stderr)
                continue
            viewer = data.get("data", {}).get("viewer") or {}
            teams = [t["key"] for t in (data.get("data", {}).get("teams", {}) or {}).get("nodes", [])]
            print(f"probe {slug}: viewer={viewer.get('email')} teams={teams}", file=sys.stderr)
            if "ELS" in teams:
                chosen = (slug, tok)
                break
        if chosen is None:
            print("no token has access to team ELS", file=sys.stderr)
            sys.exit(1)
        print(f"using token from workspace {chosen[0]}", file=sys.stderr)
        token = chosen[1]
        for ident in identifiers:
            resp = await client.post(
                LINEAR_GRAPHQL_URL,
                json={"query": ISSUE_QUERY, "variables": {"id": ident}},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code != 200:
                print(f"\n=== {ident} === HTTP {resp.status_code}\n{resp.text}\n")
                continue
            payload = resp.json()
            if payload.get("errors"):
                print(f"\n=== {ident} === errors: {json.dumps(payload['errors'], indent=2)}\n")
                continue
            issue = payload.get("data", {}).get("issue")
            if not issue:
                print(f"\n=== {ident} === not found\n")
                continue
            print(f"\n=== {ident} ===")
            print(f"title:    {issue['title']}")
            print(f"state:    {issue['state']['name']} ({issue['state']['type']})")
            print(f"priority: {issue.get('priorityLabel')}")
            print(f"team:     {issue['team']['key']}")
            project = issue.get("project")
            if project:
                print(f"project:  {project['name']}")
            print(f"url:      {issue['url']}")
            print(f"assignee: {issue.get('assignee', {}).get('name') if issue.get('assignee') else '-'}")
            labels = [n["name"] for n in issue.get("labels", {}).get("nodes", [])]
            if labels:
                print(f"labels:   {', '.join(labels)}")
            parent = issue.get("parent")
            if parent:
                print(f"parent:   {parent['identifier']} — {parent['title']}")
            print(f"created:  {issue['createdAt']}")
            print(f"updated:  {issue['updatedAt']}")
            print()
            print("--- description ---")
            print(issue.get("description") or "(empty)")
            comments = issue.get("comments", {}).get("nodes", [])
            if comments:
                print()
                print(f"--- comments ({len(comments)}) ---")
                for c in comments:
                    user = c.get("user", {}) or {}
                    print(f"\n[{c['createdAt']}] {user.get('name', '?')}:")
                    print(c["body"])


if __name__ == "__main__":
    args = sys.argv[1:] or ["ELS-70", "ELS-71"]
    asyncio.run(main(args))
