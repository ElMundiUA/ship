import fs from "node:fs";
import path from "node:path";

/**
 * Filesystem-backed blog loader.
 *
 * Posts are authored as Markdown files under `landing/content/blog/*.md`
 * with a small YAML frontmatter block:
 *
 *   ---
 *   title: ...
 *   slug: my-post
 *   date: 2026-04-22
 *   kicker: Build in public
 *   description: short sentence for the index tile and <meta description>
 *   reading_time: 9
 *   author: Denys Kuzin
 *   tags: [build-in-public, infra]
 *   ---
 *
 *   (body markdown)
 *
 * We keep the parser intentionally tiny — the book uses the same
 * react-markdown pipeline, no need for gray-matter. Array values use
 * the `[a, b, c]` bracket syntax; everything else is a plain string or
 * number.
 */

export type BlogMeta = {
  slug: string;
  title: string;
  description: string;
  kicker: string;
  date: string;
  readingTime: number;
  author: string;
  tags: string[];
};

export type BlogPost = BlogMeta & {
  body: string;
};

const BLOG_DIR = path.join(process.cwd(), "content", "blog");

function parseFrontmatter(raw: string): { data: Record<string, string | number | string[]>; body: string } {
  if (!raw.startsWith("---\n")) {
    return { data: {}, body: raw };
  }
  const end = raw.indexOf("\n---", 4);
  if (end === -1) return { data: {}, body: raw };
  const fmBlock = raw.slice(4, end);
  const rest = raw.slice(end + 4).replace(/^\n/, "");
  const data: Record<string, string | number | string[]> = {};
  for (const line of fmBlock.split("\n")) {
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
    // Strip wrapping quotes if present.
    const unquoted = rawValue.replace(/^['"]|['"]$/g, "");
    const asNum = Number(unquoted);
    data[key] = Number.isFinite(asNum) && unquoted !== "" && !Number.isNaN(asNum) && /^-?\d+(\.\d+)?$/.test(unquoted)
      ? asNum
      : unquoted;
  }
  return { data, body: rest };
}

function toMeta(slug: string, data: Record<string, string | number | string[]>): BlogMeta {
  const asString = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
  const asNumber = (v: unknown, fallback = 0): number => (typeof v === "number" ? v : fallback);
  const asStringArray = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
  return {
    slug,
    title: asString(data.title, slug),
    description: asString(data.description),
    kicker: asString(data.kicker, "Build in public"),
    date: asString(data.date, ""),
    readingTime: asNumber(data.reading_time, 0),
    author: asString(data.author, "Denys Kuzin"),
    tags: asStringArray(data.tags),
  };
}

export function listBlogPosts(): BlogMeta[] {
  if (!fs.existsSync(BLOG_DIR)) return [];
  return fs
    .readdirSync(BLOG_DIR)
    .filter((f) => f.endsWith(".md"))
    .map((file) => {
      const slug = file.replace(/\.md$/, "");
      const raw = fs.readFileSync(path.join(BLOG_DIR, file), "utf-8");
      const { data } = parseFrontmatter(raw);
      return toMeta(slug, data);
    })
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}

export function getBlogPost(slug: string): BlogPost | null {
  const fp = path.join(BLOG_DIR, `${slug}.md`);
  if (!fs.existsSync(fp)) return null;
  const raw = fs.readFileSync(fp, "utf-8");
  const { data, body } = parseFrontmatter(raw);
  return { ...toMeta(slug, data), body };
}

export function formatBlogDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
}
