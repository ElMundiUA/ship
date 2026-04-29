import fs from "node:fs";
import path from "node:path";
import type { MetadataRoute } from "next";
import { listBlogPosts } from "@/lib/blog";
import { listDocumentationPages } from "@/lib/documentation-fs";
import { loadPatternsManifest } from "@/lib/patterns";
import { repoRoot } from "@/lib/repo-path";
import { resolveMetadataBase } from "@/lib/site-url";

/**
 * App Router sitemap.
 *
 * Enumerates the marketing routes plus every dynamic route that has a
 * `generateStaticParams` (blog posts, docs pages, policies/procedures).
 * `lastModified` uses the source file's mtime where we have
 * one; static marketing pages fall back to the build date.
 */

type Priority = 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 1;
type ChangeFreq = "always" | "hourly" | "daily" | "weekly" | "monthly" | "yearly" | "never";

const BUILD_DATE = new Date();

function safeMtime(absPath: string): Date {
  try {
    return fs.statSync(absPath).mtime;
  } catch {
    return BUILD_DATE;
  }
}

function parseISO(date: string): Date {
  if (!date) return BUILD_DATE;
  const d = new Date(date);
  return Number.isNaN(d.getTime()) ? BUILD_DATE : d;
}

export default function sitemap(): MetadataRoute.Sitemap {
  const base = resolveMetadataBase().toString().replace(/\/$/, "");
  const root = repoRoot();
  const url = (p: string) => `${base}${p.startsWith("/") ? p : `/${p}`}`;

  const staticRoutes: { path: string; priority: Priority; changeFrequency: ChangeFreq }[] = [
    { path: "/", priority: 1.0, changeFrequency: "weekly" },
    { path: "/getting-started", priority: 0.9, changeFrequency: "weekly" },
    { path: "/kit", priority: 0.8, changeFrequency: "weekly" },
    { path: "/patterns", priority: 0.8, changeFrequency: "weekly" },
    { path: "/use-cases", priority: 0.7, changeFrequency: "monthly" },
    { path: "/use-cases/elmundi", priority: 0.6, changeFrequency: "monthly" },
    { path: "/blog", priority: 0.7, changeFrequency: "weekly" },
    { path: "/book", priority: 0.7, changeFrequency: "monthly" },
    { path: "/docs", priority: 0.8, changeFrequency: "weekly" },
  ];

  const out: MetadataRoute.Sitemap = staticRoutes.map((r) => ({
    url: url(r.path),
    lastModified: BUILD_DATE,
    changeFrequency: r.changeFrequency,
    priority: r.priority,
  }));

  /* Blog posts — frontmatter date beats file mtime, which gets bumped on
   * every prebuild when chart assets rebuild. */
  for (const post of listBlogPosts()) {
    out.push({
      url: url(`/blog/${post.slug}`),
      lastModified: parseISO(post.date),
      changeFrequency: "yearly",
      priority: 0.5,
    });
  }

  /* Docs pages — file mtime under documentation/. */
  for (const page of listDocumentationPages()) {
    if (page.slug.length === 0) continue;
    const abs = path.join(root, "documentation", page.rel);
    out.push({
      url: url(`/docs/${page.slug.join("/")}`),
      lastModified: safeMtime(abs),
      changeFrequency: "monthly",
      priority: 0.6,
    });
  }

  /* Policy/procedure detail pages. updated_at on the artifact frontmatter where
   * present, otherwise the source file mtime. */
  for (const p of loadPatternsManifest().patterns) {
    out.push({
      url: url(`/patterns/${p.id}`),
      lastModified: p.updated_at ? parseISO(p.updated_at) : safeMtime(path.join(root, p.path)),
      changeFrequency: "monthly",
      priority: 0.6,
    });
  }
  return out;
}
