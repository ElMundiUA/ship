"use client";

/**
 * DocsRepoTreePicker — pick folders/files from the activated repo's tree
 * instead of typing path prefixes by hand.
 *
 * Resource refs are just ``string[]`` — same shape the existing connector
 * accepts in ``config.paths`` (a path matches if it ``startswith`` any
 * prefix). Selecting a folder = include the folder recursively; selecting
 * an individual file = include just that file.
 *
 * The picker collapses the flat tree returned by the backend into an
 * expandable hierarchy. Folders that contain no doc files are pruned
 * server-side, so the visible tree is always actionable.
 */

import { useEffect, useMemo, useState } from "react";

import type { ApiDocsRepoTreeNode } from "@/lib/api/client";

type FetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | {
      kind: "ready";
      nodes: ApiDocsRepoTreeNode[];
      ref: string;
      truncated: boolean;
    }
  | { kind: "error"; message: string };

type TreeNode = {
  path: string;
  name: string;
  type: "tree" | "blob";
  children: TreeNode[];
};

export function DocsRepoTreePicker({
  workspaceId,
  repoId,
  value,
  onChange,
}: {
  workspaceId: string;
  repoId: string;
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const [state, setState] = useState<FetchState>({ kind: "idle" });
  const [expanded, setExpanded] = useState<Set<string>>(new Set([""]));

  useEffect(() => {
    if (!repoId) return;
    let cancelled = false;
    setState({ kind: "loading" });
    (async () => {
      try {
        const params = new URLSearchParams({ workspaceId, repoId });
        const resp = await fetch(`/api/knowledge/docs-repo-tree?${params.toString()}`, {
          method: "GET",
        });
        if (cancelled) return;
        if (!resp.ok) {
          const payload = await resp.json().catch(() => ({}));
          const message =
            typeof payload?.error === "string" ? payload.error : `HTTP ${resp.status}`;
          setState({ kind: "error", message });
          return;
        }
        const data = (await resp.json()) as {
          nodes: ApiDocsRepoTreeNode[];
          ref: string;
          truncated: boolean;
        };
        setState({ kind: "ready", nodes: data.nodes, ref: data.ref, truncated: data.truncated });
        // Auto-expand top-level folders that look like doc roots so the
        // picker is useful on first paint.
        const autoExpand = new Set<string>([""]);
        for (const node of data.nodes) {
          if (node.type === "tree" && /^(docs|documentation|guides|wiki)$/i.test(node.name)) {
            autoExpand.add(node.path);
          }
        }
        setExpanded(autoExpand);
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to load tree",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [repoId, workspaceId]);

  const tree = useMemo<TreeNode[]>(() => {
    if (state.kind !== "ready") return [];
    return buildTree(state.nodes);
  }, [state]);

  const selectedSet = useMemo(() => new Set(value), [value]);

  function isSelected(path: string): boolean {
    if (selectedSet.has(path)) return true;
    // A path is also "selected" if any of its prefixes is in the selected set
    // (e.g. selecting "docs" implicitly selects "docs/setup.md").
    for (const sel of selectedSet) {
      if (path.startsWith(`${sel}/`)) return true;
    }
    return false;
  }

  function toggle(path: string) {
    if (selectedSet.has(path)) {
      onChange(value.filter((existing) => existing !== path));
      return;
    }
    // If a parent is already selected, unselecting via a child doesn't
    // make sense — surface "implicit" selection by not adding duplicates.
    const parentSelected = value.some((sel) => path.startsWith(`${sel}/`));
    if (parentSelected) return;
    // Drop any descendants of this path before adding it (selecting a
    // folder makes the previously-checked individual files redundant).
    const filtered = value.filter((sel) => !sel.startsWith(`${path}/`));
    onChange([...filtered, path]);
  }

  function toggleExpanded(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  return (
    <div className="space-y-3" data-testid="docs-repo-tree-picker">
      {state.kind === "loading" && (
        <p className="text-xs text-white/55">Loading repository tree…</p>
      )}
      {state.kind === "error" && (
        <p className="text-xs text-coral">{state.message}</p>
      )}
      {state.kind === "ready" && state.nodes.length === 0 && (
        <p className="text-xs text-white/55">
          No doc files (.md, .mdx, .rst, .txt, .adoc) found in this repo.
        </p>
      )}

      {state.kind === "ready" && tree.length > 0 && (
        <>
          {value.length > 0 && (
            <div className="flex flex-wrap gap-1.5 border-t border-white/5 pt-3">
              {value.map((path) => (
                <span
                  key={path}
                  className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/10 px-2 py-0.5 text-[11px] text-aqua"
                >
                  <span className="max-w-[28ch] truncate">{path}</span>
                  <button
                    type="button"
                    onClick={() => toggle(path)}
                    className="text-aqua/70 hover:text-aqua"
                    aria-label={`Remove ${path}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="max-h-72 overflow-y-auto rounded border border-white/10 bg-black/30 px-1 py-1">
            {tree.map((node) => (
              <TreeRow
                key={node.path}
                node={node}
                depth={0}
                expanded={expanded}
                onToggleExpanded={toggleExpanded}
                isSelected={isSelected}
                onToggle={toggle}
              />
            ))}
          </div>

          {state.truncated && (
            <p className="text-[11px] text-amber-200">
              GitHub truncated this tree (very large repo). Some files may not be visible — paste the path manually under Advanced if you need them.
            </p>
          )}
        </>
      )}

      <p className="text-[11px] text-white/45">
        Select folders to ingest them recursively, or individual files.
        ``.md``, ``.mdx``, ``.rst``, ``.txt``, ``.adoc`` are picked up. Branch:{" "}
        <span className="font-mono text-white/70">
          {state.kind === "ready" ? state.ref.slice(0, 7) : "…"}
        </span>
        .
      </p>
    </div>
  );
}


function TreeRow({
  node,
  depth,
  expanded,
  onToggleExpanded,
  isSelected,
  onToggle,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  onToggleExpanded: (path: string) => void;
  isSelected: (path: string) => boolean;
  onToggle: (path: string) => void;
}) {
  const isExpanded = expanded.has(node.path);
  const checked = isSelected(node.path);
  const isDir = node.type === "tree";
  return (
    <div>
      <div
        className="flex items-center gap-2 rounded px-2 py-1 text-xs hover:bg-white/[0.04]"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {isDir ? (
          <button
            type="button"
            onClick={() => onToggleExpanded(node.path)}
            className="w-3 text-white/40 hover:text-white/80"
            aria-label={isExpanded ? "Collapse" : "Expand"}
          >
            {isExpanded ? "▾" : "▸"}
          </button>
        ) : (
          <span className="w-3" />
        )}
        <input
          type="checkbox"
          checked={checked}
          onChange={() => onToggle(node.path)}
          className="accent-aqua"
          aria-label={node.path}
        />
        <span className={`w-3 text-center ${isDir ? "text-aqua/70" : "text-white/35"}`}>
          {isDir ? "▦" : "•"}
        </span>
        <span className={isDir ? "font-semibold text-white/85" : "text-white/75"}>
          {node.name}
        </span>
      </div>
      {isDir && isExpanded && node.children.length > 0 && (
        <div>
          {node.children.map((child) => (
            <TreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggleExpanded={onToggleExpanded}
              isSelected={isSelected}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}


function buildTree(nodes: ApiDocsRepoTreeNode[]): TreeNode[] {
  // The backend returns a flat list of folders + doc files (folders
  // without doc descendants are already dropped). Build the parent/child
  // graph by splitting on "/" so the picker renders a real hierarchy.
  const byPath = new Map<string, TreeNode>();
  const roots: TreeNode[] = [];
  // Sort so parents are inserted before children regardless of input order.
  const sorted = [...nodes].sort((a, b) => a.path.localeCompare(b.path));
  for (const entry of sorted) {
    const node: TreeNode = {
      path: entry.path,
      name: entry.name,
      type: entry.type,
      children: [],
    };
    byPath.set(entry.path, node);
    const lastSlash = entry.path.lastIndexOf("/");
    if (lastSlash === -1) {
      roots.push(node);
      continue;
    }
    const parent = byPath.get(entry.path.slice(0, lastSlash));
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}
