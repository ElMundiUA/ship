#!/usr/bin/env python3
"""
RFC-0005 / Wave 1 — migrate v1 artifacts (manifest.json + scattered bodies)
into v2 layout: artifacts/<kind>/<id>/ARTIFACT.md (frontmatter as SoT).

Side effects:
  - Reads patterns/, workflows/, tools/, collections/ manifest.json.
  - Reads body files referenced by each entry's `path`.
  - Writes artifacts/<kind>/<id>/ARTIFACT.md with full v2 frontmatter
    (universal block + kind-specific spec:).
  - Writes documentation/rfc/rfc-0005-artifact-folder-spec-v2/inventory.csv
    as a Wave-0 audit map (old_path, new_path, sha, status).
  - Computes folder content_sha256 over the resulting ARTIFACT.md alone
    on first migration; sibling files (examples/, tests/) bump it later.
  - Idempotent: rerunning produces the same tree (sha may shift only if
    body files change upstream).

This script does NOT delete old manifest.json files or old body
locations — that is a separate, explicit cleanup step.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

OLD_MANIFESTS = {
    "patterns": REPO_ROOT / "patterns" / "manifest.json",
    "workflows": REPO_ROOT / "workflows" / "manifest.json",
    "tools": REPO_ROOT / "tools" / "manifest.json",
    "collections": REPO_ROOT / "collections" / "manifest.json",
}

NEW_ROOT = REPO_ROOT / "artifacts"
INVENTORY_PATH = (
    REPO_ROOT
    / "documentation"
    / "rfc"
    / "rfc-0005-artifact-folder-spec-v2"
    / "inventory.csv"
)

KIND_TO_LIST_KEY = {
    "patterns": "patterns",
    "workflows": "workflows",
    "tools": "tools",
    "collections": "collections",
}

KIND_SINGULAR = {
    "patterns": "pattern",
    "workflows": "workflow",
    "tools": "tool",
    "collections": "collection",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_existing_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Naive YAML frontmatter parser: only k:v scalars and `[...]` lists.

    Good enough for the small set of collections that already carry
    frontmatter today. Anything we cannot parse becomes part of the body.
    """

    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm_block = text[4:end]
    body = text[end + 5 :]
    fm: dict[str, Any] = {}
    for raw in fm_block.split("\n"):
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                fm[key] = []
                continue
            fm[key] = [item.strip().strip("\"'") for item in inner.split(",")]
            continue
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        fm[key] = val
    return fm, body


def scalar_yaml(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[" + ", ".join(scalar_yaml(v) for v in value) + "]"
    s = str(value)
    if any(ch in s for ch in (":", "#", "{", "}", "[", "]", ",", "&", "*", "!")) or s.strip() != s:
        return json.dumps(s, ensure_ascii=False)
    if s in ("true", "false", "null", "yes", "no") or s.replace(".", "", 1).isdigit():
        return json.dumps(s, ensure_ascii=False)
    return s


def emit_yaml_block(d: dict[str, Any], indent: int = 0) -> str:
    out: list[str] = []
    pad = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            out.append(f"{pad}{k}:")
            out.append(emit_yaml_block(v, indent + 1))
        elif isinstance(v, list) and v and isinstance(v[0], (str, int, float, bool)):
            out.append(f"{pad}{k}: {scalar_yaml(v)}")
        elif isinstance(v, list) and not v:
            out.append(f"{pad}{k}: []")
        else:
            out.append(f"{pad}{k}: {scalar_yaml(v)}")
    return "\n".join(out)


def make_description(entry: dict, kind: str, body_lines: list[str]) -> str:
    """Compose a SKILL.md-style description.

    Phase-1: take the manifest summary as the WHAT, append a generic
    'Use when ...' WHEN clause derived from kind + tags + group. A future
    pass (Wave 3, manual edit) tightens each one to be uniquely
    discoverable.
    """

    summary = (entry.get("summary") or "").strip().rstrip(".")
    tags = entry.get("tags") or []
    group = entry.get("group") or ""
    if kind == "pattern":
        when = (
            "Use when an agent picks a "
            f"{group or 'role'} slot in a Ship lane, when wiring this prompt "
            f"into a scheduled workflow, or when the catalog tags "
            f"({', '.join(tags) or 'role/lane'}) match the current task."
        )
    elif kind == "workflow":
        when = (
            "Use when designing the corresponding lane in a Ship cron grid, "
            "when adapting CI to enforce this scheduler intent, or when "
            f"reviewing automation that touches {', '.join(tags) or 'this surface'}."
        )
    elif kind == "tool":
        when = (
            "Use when integrating this surface into a Ship setup, when "
            "evaluating vendor neutrality for a procurement, or when an "
            "adapter under "
            f"{group or 'this capability'} needs to call into it."
        )
    elif kind == "collection":
        subkind = entry.get("group") or "bundle"
        when = (
            f"Use when bootstrapping a Ship project that matches this "
            f"{subkind} shape, when picking a starter set with `shipctl init`, "
            "or when the addendums or presets it composes need updating."
        )
    else:
        when = "Use when this artifact's tags match the current task."
    return (summary + ". " if summary else "") + when


def derive_pattern_spec(entry: dict, body: str) -> dict:
    install_target = entry["path"]
    spec: dict[str, Any] = {"install_target": install_target}
    role_hint = entry.get("group") or ""
    if role_hint == "cloud-agent":
        role = entry["id"].replace("cloud-", "")
        if role and role != "base":
            spec["role"] = role
    if "{{" in body:
        spec["template"] = True
    return spec


def derive_workflow_spec(entry: dict) -> dict:
    return {
        "intent": "cron",
        "runtime": "github-actions",
        "install_target": f".github/workflows/{entry['id']}.yml",
    }


def derive_tool_spec(entry: dict) -> dict:
    group = entry.get("group") or "platform"
    capability = {
        "tracker": "tracker",
        "ci": "ci",
        "e2e": "e2e",
        "agents": "agents",
        "platform": "platform",
    }.get(group, "platform")
    return {
        "capability": capability,
        "install_target": entry["path"],
    }


def derive_collection_spec(entry: dict, existing_fm: dict) -> dict:
    spec: dict[str, Any] = {}
    eid = entry["id"]
    if eid.startswith("preset-"):
        spec["subkind"] = "preset"
    elif eid.startswith("addendum-"):
        spec["subkind"] = "addendum"
    elif eid.startswith("agent-rules-"):
        spec["subkind"] = "agent-rules"
    else:
        spec["subkind"] = existing_fm.get("subkind") or "starter"
    for key in (
        "applies_to",
        "compatible_trackers",
        "compatible_ci",
        "compatible_agents",
        "required_tools",
        "optional_tools",
        "addendums",
        "addendums_compatible_with",
        "regulatory_frameworks",
        "preset_id",
        "addendum_id",
    ):
        if key in existing_fm:
            spec[key] = existing_fm[key]
    spec.setdefault("install_target", entry["path"])
    return spec


def build_frontmatter(
    entry: dict, kind: str, existing_fm: dict, body: str, content_sha: str
) -> dict:
    artifact_kind = KIND_SINGULAR[kind]
    fm: dict[str, Any] = {
        "artifact_kind": artifact_kind,
        "id": entry["id"],
        "name": entry.get("title") or entry["id"],
        "description": make_description(entry, artifact_kind, body.splitlines()),
        "version": entry.get("version") or "1.0.0",
        "channel": entry.get("channel") or "stable",
        "min_shipctl": entry.get("min_shipctl") or "0.3.0",
        "updated_at": entry.get("updated_at")
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "content_sha256": content_sha,
        "deprecated": bool(entry.get("deprecated", False)),
        "replaced_by": entry.get("replaced_by"),
        "yanked": bool(entry.get("yanked", False)),
        "group": entry.get("group") or "",
        "tags": list(entry.get("tags") or []),
        "authors": ["@elmundi/ship-core"],
        "license": "Apache-2.0",
    }
    if artifact_kind == "pattern":
        fm["spec"] = derive_pattern_spec(entry, body)
    elif artifact_kind == "workflow":
        fm["spec"] = derive_workflow_spec(entry)
    elif artifact_kind == "tool":
        fm["spec"] = derive_tool_spec(entry)
    elif artifact_kind == "collection":
        fm["spec"] = derive_collection_spec(entry, existing_fm)
    return fm


def render_artifact_md(fm: dict, body: str) -> str:
    desc = fm.pop("description")
    spec = fm.pop("spec", None)
    fm_lines: list[str] = ["---"]
    for k, v in fm.items():
        fm_lines.append(f"{k}: {scalar_yaml(v)}")
    fm_lines.append("description: >-")
    for line in desc.split("\n"):
        fm_lines.append(f"  {line}")
    if spec is not None:
        fm_lines.append("spec:")
        fm_lines.append(emit_yaml_block(spec, indent=1))
    fm_lines.append("---")
    fm.setdefault("description", desc)
    if spec is not None:
        fm["spec"] = spec
    body = body.lstrip("\n")
    return "\n".join(fm_lines) + "\n\n" + body


def migrate() -> int:
    NEW_ROOT.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    inventory_rows: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    failures = 0

    for kind_plural, mpath in OLD_MANIFESTS.items():
        if not mpath.exists():
            print(f"[skip] missing manifest: {mpath}", file=sys.stderr)
            continue
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        list_key = KIND_TO_LIST_KEY[kind_plural]
        entries = manifest.get(list_key, [])
        for entry in entries:
            entry_id = entry["id"]
            body_path_rel = entry["path"]
            body_path = REPO_ROOT / body_path_rel
            inventory_row = {
                "kind": KIND_SINGULAR[kind_plural],
                "id": entry_id,
                "old_path": body_path_rel,
                "new_path": f"artifacts/{kind_plural}/{entry_id}/ARTIFACT.md",
                "old_sha256": entry.get("content_sha256", "")[:16],
                "status": "",
                "notes": "",
            }
            if (kind_plural, entry_id) in seen_pairs:
                inventory_row["status"] = "duplicate-id"
                inventory_row["notes"] = "second occurrence of same id in old manifest; skipped"
                inventory_rows.append(inventory_row)
                failures += 1
                continue
            seen_pairs.add((kind_plural, entry_id))

            if not body_path.exists():
                inventory_row["status"] = "missing-body"
                inventory_row["notes"] = f"body referenced by manifest does not exist on disk: {body_path}"
                inventory_rows.append(inventory_row)
                failures += 1
                continue

            raw = body_path.read_text(encoding="utf-8")
            existing_fm, body = parse_existing_frontmatter(raw)

            target_dir = NEW_ROOT / kind_plural / entry_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target_md = target_dir / "ARTIFACT.md"

            placeholder_sha = "0" * 64
            fm = build_frontmatter(entry, kind_plural, existing_fm, body, placeholder_sha)
            preliminary = render_artifact_md(fm, body)
            preliminary_no_sha = preliminary.replace(placeholder_sha, "")
            real_sha = sha256_bytes(preliminary_no_sha.encode("utf-8"))
            fm["content_sha256"] = real_sha
            final = render_artifact_md(fm, body)

            target_md.write_text(final, encoding="utf-8")

            inventory_row["status"] = "migrated"
            inventory_row["notes"] = f"new sha={real_sha[:16]}…"
            inventory_rows.append(inventory_row)

    INVENTORY_PATH.write_text(
        "",
        encoding="utf-8",
    )
    with INVENTORY_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["kind", "id", "old_path", "new_path", "old_sha256", "status", "notes"]
        )
        writer.writeheader()
        writer.writerows(inventory_rows)

    migrated = sum(1 for r in inventory_rows if r["status"] == "migrated")
    print(f"migrate: {migrated} artifacts written under {NEW_ROOT.relative_to(REPO_ROOT)}/")
    print(f"migrate: inventory at {INVENTORY_PATH.relative_to(REPO_ROOT)}")
    if failures:
        print(f"migrate: {failures} failures (see inventory.csv `status`)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(migrate())
