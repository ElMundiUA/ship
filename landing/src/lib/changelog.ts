import fs from "node:fs";
import path from "node:path";

/**
 * Filesystem-backed changelog loader.
 *
 * Each release / weekly entry is one Markdown file under
 * ``landing/content/changelog/*.md`` with a frontmatter block:
 *
 *   ---
 *   slug: 2026-05-07-navigator-goes-agentic
 *   date: 2026-05-07                  # ISO yyyy-mm-dd; sort key.
 *   title: Navigator goes agentic
 *   summary: One-line lead used in the hero.
 *   kicker: Release                   # small label above the title.
 *   hero_image: /changelog/2026-05/navigator-agentic.webp   # optional
 *   hero_alt: Description of the image (a11y).               # required if hero_image
 *   hero_video: https://www.loom.com/embed/...               # optional
 *   ---
 *
 *   (markdown body — `## Highlights`, `## Improvements`, `## Fixes`)
 *
 * The body uses the same react-markdown pipeline as the blog
 * (see ``BlogMarkdown`` + ``preprocessBlogMarkdown``). PR references
 * in body text written as bare ``#155`` are auto-linked.
 *
 * The page renders entries sorted by ``date`` desc. No future-publish
 * gating — the changelog is meant to be edited as releases ship; if
 * you want to stage a draft, keep it on a branch.
 */

export type ChangelogMeta = {
  slug: string;
  date: string;
  title: string;
  summary: string;
  kicker: string;
  heroImage: string;
  heroAlt: string;
  heroVideo: string;
  prs: number[];
};

export type ChangelogEntry = ChangelogMeta & {
  body: string;
};

const CHANGELOG_DIR = path.join(process.cwd(), "content", "changelog");

function parseFrontmatter(raw: string): {
  data: Record<string, string | number | string[]>;
  body: string;
} {
  if (!raw.startsWith("---\n")) return { data: {}, body: raw };
  const end = raw.indexOf("\n---", 4);
  if (end === -1) return { data: {}, body: raw };
  const block = raw.slice(4, end);
  const rest = raw.slice(end + 4).replace(/^\n/, "");
  const data: Record<string, string | number | string[]> = {};
  for (const line of block.split("\n")) {
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$/);
    if (!m) continue;
    const key = m[1];
    const rawValue = m[2].trim();
    if (!rawValue) continue;
    if (rawValue.startsWith("[") && rawValue.endsWith("]")) {
      data[key] = rawValue
        .slice(1, -1)
        .split(",")
        .map((s) => s.trim().replace(/^['"]|['"]$/g, ""))
        .filter(Boolean);
      continue;
    }
    const unquoted = rawValue.replace(/^['"]|['"]$/g, "");
    data[key] = unquoted;
  }
  return { data, body: rest };
}

function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function asNumberArray(v: unknown): number[] {
  if (!Array.isArray(v)) return [];
  return v
    .map((s) => (typeof s === "string" ? Number(s) : typeof s === "number" ? s : NaN))
    .filter((n) => Number.isFinite(n) && n > 0);
}

function toMeta(slug: string, data: Record<string, string | number | string[]>): ChangelogMeta {
  return {
    slug: asString(data.slug, slug),
    date: asString(data.date, ""),
    title: asString(data.title, slug),
    summary: asString(data.summary),
    kicker: asString(data.kicker, "Release"),
    heroImage: asString(data.hero_image),
    heroAlt: asString(data.hero_alt),
    heroVideo: asString(data.hero_video),
    prs: asNumberArray(data.prs),
  };
}

export function listChangelogEntries(): ChangelogEntry[] {
  if (!fs.existsSync(CHANGELOG_DIR)) return [];
  const files = fs.readdirSync(CHANGELOG_DIR).filter((f) => f.endsWith(".md"));
  return files
    .map((file) => {
      const fileSlug = file.replace(/\.md$/, "");
      const raw = fs.readFileSync(path.join(CHANGELOG_DIR, file), "utf-8");
      const { data, body } = parseFrontmatter(raw);
      const meta = toMeta(fileSlug, data);
      return { ...meta, body };
    })
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}

export function formatChangelogDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}
