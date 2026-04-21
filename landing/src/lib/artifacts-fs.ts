import fs from "node:fs";
import path from "node:path";
import { repoRoot } from "@/lib/repo-path";

/**
 * Generic artifact entry shape, shared across patterns / tools /
 * collections. The catalog page components only read a handful of fields
 * (id, title, summary, path, tags, group); everything else is preserved so
 * detail pages can grow into them without another refactor.
 */
export interface ArtifactEntry {
  id: string;
  title: string;
  summary: string;
  path: string;
  tags: string[];
  group: string;
  version: string;
  content_sha256: string;
  updated_at: string;
  channel: string;
  min_shipctl: string;
  deprecated: boolean;
  replaced_by: string | null;
  yanked: boolean;
  spec: Record<string, unknown>;
}

export interface ArtifactCatalog<E extends ArtifactEntry = ArtifactEntry> {
  version: number;
  description: string;
  entries: E[];
}

const KIND_DESCRIPTIONS: Record<string, string> = {
  patterns:
    "Reviewable markdown prompts agents reach for during scheduled lanes, onboarding, and the cloud-agent grid.",
  tools:
    "Vendor-neutral integration descriptors for trackers, CI, e2e, and platform surfaces Ship snaps to.",
  collections:
    "Bundled starter sets: presets, addendums, and per-agent rules collections that compose into a Ship workspace.",
};

/**
 * Tiny YAML frontmatter parser. Supports the limited shape RFC-0005 emits:
 *   - scalar `key: value` (with optional double-quoted strings)
 *   - inline lists `key: [a, b, c]`
 *   - block scalar `key: >-\n  multi\n  line` (collapses newlines to spaces)
 *   - single-level nested block (used for `spec:`)
 *   - `null` / `true` / `false` literals
 *
 * Anything we cannot interpret stays as a raw string. The goal is to expose
 * enough metadata for the landing UI without dragging in a YAML dependency.
 */
function parseValue(raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  if (trimmed === "null" || trimmed === "~") return null;
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
    const inner = trimmed.slice(1, -1).trim();
    if (!inner) return [];
    return inner
      .split(",")
      .map((part) => part.trim())
      .map((part) => {
        if (
          (part.startsWith('"') && part.endsWith('"')) ||
          (part.startsWith("'") && part.endsWith("'"))
        ) {
          return part.slice(1, -1);
        }
        return part;
      });
  }
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function indentOf(line: string): number {
  let n = 0;
  while (n < line.length && line[n] === " ") n += 1;
  return n;
}

export function parseFrontmatter(source: string): { fm: Record<string, unknown>; body: string } {
  if (!source.startsWith("---\n")) return { fm: {}, body: source };
  const end = source.indexOf("\n---\n", 4);
  if (end === -1) return { fm: {}, body: source };
  const fmBlock = source.slice(4, end);
  const body = source.slice(end + 5);
  const fm: Record<string, unknown> = {};

  const lines = fmBlock.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim() || line.trim().startsWith("#")) {
      i += 1;
      continue;
    }
    if (indentOf(line) > 0) {
      i += 1;
      continue;
    }
    const colon = line.indexOf(":");
    if (colon === -1) {
      i += 1;
      continue;
    }
    const key = line.slice(0, colon).trim();
    const after = line.slice(colon + 1);

    if (after.trim() === "") {
      const child: Record<string, unknown> = {};
      i += 1;
      while (i < lines.length && (lines[i].trim() === "" || indentOf(lines[i]) >= 2)) {
        const cl = lines[i];
        if (cl.trim() === "") {
          i += 1;
          continue;
        }
        const sub = cl.replace(/^ {2}/, "");
        const subColon = sub.indexOf(":");
        if (subColon === -1) {
          i += 1;
          continue;
        }
        const subKey = sub.slice(0, subColon).trim();
        const subVal = sub.slice(subColon + 1);
        child[subKey] = parseValue(subVal);
        i += 1;
      }
      fm[key] = child;
      continue;
    }

    const trimmedAfter = after.trim();
    if (trimmedAfter === ">" || trimmedAfter === ">-" || trimmedAfter === "|" || trimmedAfter === "|-") {
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && (lines[i].trim() === "" || indentOf(lines[i]) >= 2)) {
        if (lines[i].trim() === "") {
          buf.push("");
        } else {
          buf.push(lines[i].replace(/^ {2}/, ""));
        }
        i += 1;
      }
      const joined =
        trimmedAfter[0] === "|"
          ? buf.join("\n")
          : buf.join(" ").replace(/\s+/g, " ").trim();
      fm[key] = joined;
      continue;
    }

    fm[key] = parseValue(after);
    i += 1;
  }

  return { fm, body };
}

function firstSentence(text: string): string {
  if (!text) return "";
  const dot = text.indexOf(". ");
  if (dot === -1) return text.trim();
  return text.slice(0, dot + 1).trim();
}

function entryFromFrontmatter(
  fm: Record<string, unknown>,
  artifactPath: string,
): ArtifactEntry {
  const description = (fm.description as string) || "";
  return {
    id: String(fm.id ?? ""),
    title: String(fm.name ?? fm.id ?? ""),
    summary: firstSentence(description),
    path: artifactPath,
    tags: Array.isArray(fm.tags) ? (fm.tags as string[]) : [],
    group: String(fm.group ?? ""),
    version: String(fm.version ?? "0.0.0"),
    content_sha256: String(fm.content_sha256 ?? ""),
    updated_at: String(fm.updated_at ?? ""),
    channel: String(fm.channel ?? "stable"),
    min_shipctl: String(fm.min_shipctl ?? ""),
    deprecated: Boolean(fm.deprecated),
    replaced_by: (fm.replaced_by as string | null) ?? null,
    yanked: Boolean(fm.yanked),
    spec: (fm.spec as Record<string, unknown>) ?? {},
  };
}

const CATALOG_CACHE = new Map<string, ArtifactCatalog>();

export function loadArtifactCatalog(kindPlural: string): ArtifactCatalog {
  const cached = CATALOG_CACHE.get(kindPlural);
  if (cached) return cached;

  const root = repoRoot();
  const dir = path.join(root, "artifacts", kindPlural);
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
    throw new Error(`artifacts/${kindPlural} not found.`);
  }
  const entries: ArtifactEntry[] = [];
  const ids = fs
    .readdirSync(dir)
    .filter((name) => fs.statSync(path.join(dir, name)).isDirectory())
    .sort();
  for (const id of ids) {
    const file = path.join(dir, id, "ARTIFACT.md");
    if (!fs.existsSync(file)) continue;
    const raw = fs.readFileSync(file, "utf8");
    const { fm } = parseFrontmatter(raw);
    if (!fm.id) continue;
    const rel = path.relative(root, file);
    entries.push(entryFromFrontmatter(fm, rel));
  }
  const catalog: ArtifactCatalog = {
    version: 2,
    description: KIND_DESCRIPTIONS[kindPlural] ?? "",
    entries,
  };
  CATALOG_CACHE.set(kindPlural, catalog);
  return catalog;
}

export function loadArtifactBody(relPath: string): string {
  const root = repoRoot();
  const candidate = path.resolve(root, relPath);
  if (!candidate.startsWith(root + path.sep) && candidate !== root) {
    throw new Error("Artifact path escapes repository root.");
  }
  if (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()) {
    throw new Error(`Artifact file missing: ${relPath}`);
  }
  const raw = fs.readFileSync(candidate, "utf8");
  if (!raw.startsWith("---\n")) return raw;
  const end = raw.indexOf("\n---\n", 4);
  if (end === -1) return raw;
  return raw.slice(end + 5).replace(/^\n+/, "");
}
