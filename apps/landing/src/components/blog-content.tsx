import Link from "next/link";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import type { ReactNode } from "react";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/cn";

/**
 * Rework-flavored prose renderer.
 *
 * The book is loud on purpose — embossed H2 cards, chapter pills, ornament
 * HRs. For the blog we want the opposite: short essays that read like
 * 37signals posts. One accent colour, lots of air, wide-set type, quiet
 * typographic H2s. The only loud element is a pull-quote, which we reuse
 * for `> callout` blocks.
 *
 * Infographics land in `<figure class="blog-chart">` wrappers emitted by
 * injectBlogCharts (see blog-markdown.mjs). They share the book's paper-
 * toned card so the warm ECharts palette survives on the dark theme.
 */

type RemarkNodeProp = { node?: unknown };

function stripRemarkNode<P extends object>(props: P): Omit<P, "node"> {
  const { node: _remarkAstNode, ...rest } = props as P & RemarkNodeProp;
  void _remarkAstNode;
  return rest;
}

function BlogH2(props: React.HTMLAttributes<HTMLHeadingElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  return (
    <h2
      {...dom}
      className={cn(
        "font-display mt-16 mb-5 scroll-mt-28 text-[1.55rem] font-bold leading-tight tracking-tight text-white sm:text-[1.8rem]",
        dom.className,
      )}
    />
  );
}

function BlogH3(props: React.HTMLAttributes<HTMLHeadingElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  return (
    <h3
      {...dom}
      className={cn(
        "font-display mt-12 mb-3 scroll-mt-28 text-lg font-bold leading-snug text-white sm:text-xl",
        dom.className,
      )}
    />
  );
}

function BlogImg(props: React.ImgHTMLAttributes<HTMLImageElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  const rawSrc = dom.src;
  const src = typeof rawSrc === "string" ? rawSrc : "";

  /* Blog infographics live in /diagrams/blog/*.svg — cream paper card
   * already declared by the parent <figure class="blog-chart"> emitted
   * by injectBlogCharts. */
  if (src.includes("/diagrams/blog/") || src.includes("/diagrams/charts/")) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- baked SVG asset in /public
      <img {...dom} alt={dom.alt ?? ""} className="mx-auto block h-auto w-full max-w-full" loading="lazy" />
    );
  }

  return (
    <figure className="not-prose my-12">
      <div className="overflow-hidden rounded-2xl border border-white/10 bg-black/30 shadow-card">
        {/* eslint-disable-next-line @next/next/no-img-element -- dynamic public asset */}
        <img {...dom} alt={dom.alt ?? ""} className="mx-auto block w-full" loading="lazy" />
      </div>
      {dom.alt ? (
        <figcaption className="mt-3 text-center text-sm text-white/50">{dom.alt}</figcaption>
      ) : null}
    </figure>
  );
}

function BlogBlockquote(props: { children?: ReactNode } & RemarkNodeProp) {
  const { children } = stripRemarkNode(props) as { children?: ReactNode };
  return (
    <blockquote className="not-prose my-10 border-l-[3px] border-aqua/70 bg-white/[0.02] py-3 pl-6 pr-4 font-display text-xl leading-snug text-white/90 sm:text-2xl">
      {children}
    </blockquote>
  );
}

function BlogHr() {
  return (
    <div className="not-prose my-14 flex items-center justify-center" role="separator">
      <div className="flex items-center gap-3 text-xs font-bold uppercase tracking-[0.3em] text-white/25">
        <span className="h-px w-10 bg-white/15" />
        <span>§</span>
        <span className="h-px w-10 bg-white/15" />
      </div>
    </div>
  );
}

function BlogLink(props: React.AnchorHTMLAttributes<HTMLAnchorElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  const href = typeof dom.href === "string" ? dom.href : "";
  if (href.startsWith("/")) {
    return (
      <Link href={href} className={cn("font-semibold text-aqua underline decoration-aqua/35 underline-offset-2 hover:decoration-aqua", dom.className)}>
        {dom.children}
      </Link>
    );
  }
  return (
    <a
      {...dom}
      className={cn("font-semibold text-aqua underline decoration-aqua/35 underline-offset-2 hover:decoration-aqua", dom.className)}
      target="_blank"
      rel="noopener noreferrer"
    />
  );
}

function BlogTable(props: React.HTMLAttributes<HTMLTableElement> & RemarkNodeProp) {
  const { children, ...rest } = stripRemarkNode(props);
  return (
    <div className="not-prose my-10 overflow-x-auto rounded-2xl border border-white/10 bg-black/30 p-1 shadow-inner">
      <table {...rest} className="min-w-full border-collapse text-left text-sm text-white/85">
        {children}
      </table>
    </div>
  );
}

function BlogThead(props: React.HTMLAttributes<HTMLTableSectionElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  return <thead {...dom} className={cn("bg-white/[0.05] text-xs uppercase tracking-wider text-aqua/90", dom.className)} />;
}

function BlogTh(props: React.HTMLAttributes<HTMLTableCellElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  return <th {...dom} className={cn("border-b border-white/15 px-4 py-3 font-semibold", dom.className)} />;
}

function BlogTd(props: React.HTMLAttributes<HTMLTableCellElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  return <td {...dom} className={cn("border-b border-white/10 px-4 py-3 align-top text-white/75", dom.className)} />;
}

const blogComponents: Partial<Components> = {
  h2: BlogH2,
  h3: BlogH3,
  img: BlogImg,
  blockquote: BlogBlockquote,
  hr: BlogHr,
  a: BlogLink,
  table: BlogTable,
  thead: BlogThead,
  th: BlogTh,
  td: BlogTd,
};

export function BlogMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]} components={blogComponents}>
      {content}
    </ReactMarkdown>
  );
}
