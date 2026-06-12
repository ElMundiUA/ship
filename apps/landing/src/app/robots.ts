import type { MetadataRoute } from "next";

/**
 * App Router robots.txt.
 *
 * Internal workspace: stay crawlable (so Googlebot can read the sitewide
 * `noindex` meta and drop already-indexed pages) but advertise no sitemap.
 * The previous sitemap pointer resolved to http://localhost:3000 in prod
 * (NEXT_PUBLIC_SITE_URL unset), which was broken anyway.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/" }],
  };
}
