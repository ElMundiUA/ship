import type { MetadataRoute } from "next";
import { resolveMetadataBase } from "@/lib/site-url";

/**
 * App Router robots.txt.
 *
 * Allow-all, with a single sitemap pointer. Same `BASE_URL` source as
 * `sitemap.ts` so a custom NEXT_PUBLIC_SITE_URL flows through to both.
 */
export default function robots(): MetadataRoute.Robots {
  const base = resolveMetadataBase().toString().replace(/\/$/, "");
  return {
    rules: [{ userAgent: "*", allow: "/" }],
    sitemap: `${base}/sitemap.xml`,
  };
}
