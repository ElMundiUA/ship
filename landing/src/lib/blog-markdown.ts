/**
 * Blog markdown preprocessor.
 *
 * Posts reference charts by slug through a custom shortcode:
 *
 *   {{chart: blog-first-twelve-days-commits | caption text }}
 *
 * which expands to the same `<figure class="blog-chart">` shape the
 * blog renderer expects (see blog-content.tsx). Charts are produced by
 * `landing/scripts/build-blog-charts.mjs` and written to
 * `landing/public/diagrams/blog/<slug>.svg`.
 *
 * We also support a standard markdown `![alt](/diagrams/blog/foo.svg)`
 * — it renders the same way — but the shortcode gives writers a nicer
 * caption slot without having to hand-write HTML.
 */

const CHART_SHORTCODE = /\{\{\s*chart:\s*([a-z0-9-]+)(?:\s*\|\s*([^}]+))?\s*\}\}/g;

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;");
}

export function expandBlogCharts(source: string): string {
  return source.replace(CHART_SHORTCODE, (_full, slug: string, caption?: string) => {
    const alt = caption ? caption.trim() : slug;
    const fig = [
      `<figure class="blog-chart not-prose">`,
      `<img src="/diagrams/blog/${slug}.svg" alt="${escapeHtml(alt)}" />`,
      caption ? `<figcaption>${escapeHtml(caption.trim())}</figcaption>` : "",
      `</figure>`,
    ]
      .filter(Boolean)
      .join("\n");
    return `\n\n${fig}\n\n`;
  });
}

export function preprocessBlogMarkdown(source: string): string {
  return expandBlogCharts(source);
}
