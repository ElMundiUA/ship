import Link from "next/link";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import type { ReactNode } from "react";
import { isValidElement } from "react";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/cn";
import { slugifyHeading } from "@/lib/docs-page-parts";

/**
 * Markdown renderer for the Docs ( /docs ).
 *
 * Different from <BookMarkdown /> on purpose: the book uses heavy bordered-card
 * H2 blocks (works for ~30 long sections of narrative); the docs are reference
 * material with many short headings, so the same shape would turn it into a
 * 2005-era wiki. Headings here are crisp typographic chunks with a thin coloured
 * rail and an anchor pilcrow on hover. Code blocks get terminal chrome.
 */

type RemarkNodeProp = { node?: unknown };

function strip<P extends object>(props: P): Omit<P, "node"> {
  const { node: _ignored, ...rest } = props as P & RemarkNodeProp;
  void _ignored;
  return rest;
}

function flatten(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flatten).join("");
  if (isValidElement(node)) {
    const p = node.props as { children?: ReactNode };
    if (p && typeof p === "object" && "children" in p) return flatten(p.children);
  }
  return "";
}

function HeadingAnchor({ id }: { id?: string }) {
  if (!id) return null;
  return (
    <a href={`#${id}`} className="docs-anchor" aria-label="Link to this section">
      #
    </a>
  );
}

function autoId(domId: string | undefined, children: ReactNode): string | undefined {
  if (domId) return domId;
  const text = flatten(children).trim();
  if (!text) return undefined;
  return slugifyHeading(text);
}

function H2(props: React.HTMLAttributes<HTMLHeadingElement> & RemarkNodeProp) {
  const dom = strip(props);
  const id = autoId(dom.id, dom.children);
  return (
    <h2 {...dom} id={id} className={cn("group", dom.className)}>
      {dom.children}
      <HeadingAnchor id={id} />
    </h2>
  );
}

function H3(props: React.HTMLAttributes<HTMLHeadingElement> & RemarkNodeProp) {
  const dom = strip(props);
  const id = autoId(dom.id, dom.children);
  return (
    <h3 {...dom} id={id} className={cn("group", dom.className)}>
      {dom.children}
      <HeadingAnchor id={id} />
    </h3>
  );
}

function H4(props: React.HTMLAttributes<HTMLHeadingElement> & RemarkNodeProp) {
  const dom = strip(props);
  const id = autoId(dom.id, dom.children);
  return (
    <h4 {...dom} id={id} className={cn("group", dom.className)}>
      {dom.children}
      <HeadingAnchor id={id} />
    </h4>
  );
}

function A(props: React.AnchorHTMLAttributes<HTMLAnchorElement> & RemarkNodeProp) {
  const dom = strip(props);
  const href = typeof dom.href === "string" ? dom.href : "";
  if (href.startsWith("/")) {
    return <Link href={href} className={dom.className}>{dom.children}</Link>;
  }
  if (href.startsWith("#")) {
    return <a {...dom} />;
  }
  return <a {...dom} target="_blank" rel="noopener noreferrer" />;
}

function Pre(props: React.HTMLAttributes<HTMLPreElement> & RemarkNodeProp) {
  const dom = strip(props);
  /* react-markdown wraps <code class="language-x"> inside <pre>; pull the
   * language tag for the chrome label. */
  let lang = "";
  if (isValidElement(dom.children)) {
    const c = (dom.children as React.ReactElement<{ className?: string }>).props;
    const cls = c?.className ?? "";
    const m = /language-([\w-]+)/.exec(cls);
    if (m) lang = m[1];
  }
  return (
    <div className="docs-code">
      <div className="docs-code-bar">
        <span className="docs-code-dots">
          <span className="bg-coral/85" />
          <span className="bg-sun/85" />
          <span className="bg-aqua/85" />
        </span>
        <span className="docs-code-lang">{lang || "code"}</span>
      </div>
      <pre {...dom} />
    </div>
  );
}

function Table(props: React.HTMLAttributes<HTMLTableElement> & RemarkNodeProp) {
  const { children, ...rest } = strip(props);
  return (
    <div className="docs-table-wrap">
      <table {...rest}>{children}</table>
    </div>
  );
}

function Img(props: React.ImgHTMLAttributes<HTMLImageElement> & RemarkNodeProp) {
  const dom = strip(props);
  return (
    <figure className="not-prose my-8">
      <div className="overflow-hidden rounded-2xl border border-white/10 bg-black/40 p-3 shadow-card">
        {/* eslint-disable-next-line @next/next/no-img-element -- /public asset */}
        <img {...dom} alt={dom.alt ?? ""} className="mx-auto block h-auto w-full" loading="lazy" />
      </div>
      {dom.alt ? (
        <figcaption className="mt-2 text-center text-xs text-white/45">{dom.alt}</figcaption>
      ) : null}
    </figure>
  );
}

function Blockquote(props: { children?: ReactNode } & RemarkNodeProp) {
  const { children } = strip(props) as { children?: ReactNode };
  const text = flatten(children).trim();
  /* Tip / Note callouts (rendered from Material admonitions) */
  if (/^Tip\b/.test(text)) {
    return (
      <aside className="not-prose my-7 rounded-2xl border border-aqua/25 bg-gradient-to-br from-aqua/[0.10] via-white/[0.03] to-transparent p-4 sm:p-5">
        <div className="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-aqua">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-aqua shadow-[0_0_10px_rgba(207,169,107,0.8)]" />
          Tip
        </div>
        <div className="text-sm leading-relaxed text-white/82 [&_a]:font-semibold [&_a]:text-aqua [&_strong]:text-white">
          {children}
        </div>
      </aside>
    );
  }
  if (/^Note\b/.test(text)) {
    return (
      <aside className="not-prose my-7 rounded-2xl border border-lilac/25 bg-gradient-to-br from-lilac/[0.10] via-white/[0.03] to-transparent p-4 sm:p-5">
        <div className="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-lilac">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-lilac shadow-[0_0_10px_rgba(179,136,255,0.7)]" />
          Note
        </div>
        <div className="text-sm leading-relaxed text-white/82 [&_a]:font-semibold [&_a]:text-aqua [&_strong]:text-white">
          {children}
        </div>
      </aside>
    );
  }
  return <blockquote>{children}</blockquote>;
}

const components: Partial<Components> = {
  h2: H2,
  h3: H3,
  h4: H4,
  a: A,
  pre: Pre,
  table: Table,
  img: Img,
  blockquote: Blockquote,
};

export function DocsMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]} components={components}>
      {content}
    </ReactMarkdown>
  );
}
