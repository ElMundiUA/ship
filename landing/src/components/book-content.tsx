import Link from "next/link";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import type { ReactNode } from "react";
import { isValidElement } from "react";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { BookSectionInsert } from "@/components/book-decorations";
import { SdlcLaneDiagram, SystemArchitectureDiagram } from "@/components/book-diagrams";
import { cn } from "@/lib/cn";

/** react-markdown passes AST `node`; spreading it onto DOM becomes `node="[object Object]"` and breaks hydration. */
type RemarkNodeProp = { node?: unknown };

function stripRemarkNode<P extends object>(props: P): Omit<P, "node"> {
  const { node: _remarkAstNode, ...rest } = props as P & RemarkNodeProp;
  void _remarkAstNode;
  return rest;
}

function flattenText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenText).join("");
  if (isValidElement(node)) {
    const p = node.props as { children?: ReactNode };
    if (p && typeof p === "object" && "children" in p) {
      return flattenText(p.children);
    }
  }
  return "";
}

function BookH2(props: React.HTMLAttributes<HTMLHeadingElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  const id = dom.id;
  return (
    <div className="not-prose">
      <h2
        {...dom}
        className={cn(
          "book-heading font-display text-2xl font-bold tracking-tight text-white sm:text-[1.65rem]",
          "relative overflow-hidden rounded-2xl border border-white/12",
          "bg-gradient-to-br from-white/[0.1] via-white/[0.03] to-transparent",
          "px-5 py-5 shadow-card scroll-mt-28",
          dom.className,
        )}
      />
      {id ? <BookSectionInsert id={id} /> : null}
    </div>
  );
}

function BookH3(props: React.HTMLAttributes<HTMLHeadingElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  const label = flattenText(dom.children);
  const ch = label.match(/Chapter\s+(\d+)/i);
  return (
    <div className="not-prose mt-14 flex gap-3 sm:gap-5">
      {ch ? (
        <div
          className="hidden w-11 shrink-0 flex-col items-center rounded-2xl border border-white/10 bg-gradient-to-b from-aqua/15 to-lilac/10 py-3 sm:flex"
          aria-hidden
        >
          <span className="text-[10px] font-bold uppercase tracking-widest text-white/40">Ch.</span>
          <span className="font-display text-lg font-bold text-aqua">{ch[1]}</span>
        </div>
      ) : (
        <div className="hidden w-2 shrink-0 rounded-full bg-gradient-to-b from-aqua/40 to-lilac/30 sm:block" aria-hidden />
      )}
      <h3
        {...dom}
        className={cn(
          "flex-1 font-display text-lg font-bold leading-snug text-white sm:text-xl",
          dom.className,
        )}
      />
    </div>
  );
}

function BookImg(props: React.ImgHTMLAttributes<HTMLImageElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  const rawSrc = dom.src;
  const src = typeof rawSrc === "string" ? rawSrc : "";
  if (src.includes("architecture.svg")) {
    return <SystemArchitectureDiagram caption={dom.alt ?? undefined} />;
  }
  if (src.includes("sdlc-linear-states.svg")) {
    return <SdlcLaneDiagram caption={dom.alt ?? undefined} />;
  }

  /* ECharts infographics (book-morning-metrics.svg, book-cost-envelope.svg, …)
   * ship with the print palette baked in (cream paper, ink lines). On the
   * dark `/book` page we drop them on a paper-toned card so the warm palette
   * reads correctly — the parent `<figure class="book-chart">` (emitted by
   * injectBookCharts) provides the layout slot, this branch supplies the
   * styled <img>. We deliberately bypass the "ship / diagram" titlebar
   * wrapper because charts already carry their own legend + figcaption. */
  if (src.includes("/diagrams/charts/")) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- baked SVG asset in /public
      <img
        {...dom}
        alt={dom.alt ?? ""}
        className="mx-auto block h-auto w-full max-w-full"
        loading="lazy"
      />
    );
  }

  return (
    <figure className="not-prose my-14">
      <div className="relative overflow-hidden rounded-2xl border border-white/12 bg-black/40 shadow-card">
        <div className="flex items-center gap-2 border-b border-white/10 bg-white/[0.05] px-3 py-2.5">
          <span className="inline-flex gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-coral/85" />
            <span className="h-2.5 w-2.5 rounded-full bg-sun/85" />
            <span className="h-2.5 w-2.5 rounded-full bg-aqua/85" />
          </span>
          <span className="ml-2 font-mono text-[10px] uppercase tracking-widest text-white/35">ship / diagram</span>
        </div>
        <div className="p-2 sm:p-4">
          {/* eslint-disable-next-line @next/next/no-img-element -- dynamic public diagram */}
          <img {...dom} alt={dom.alt ?? ""} className="mx-auto w-full max-w-3xl rounded-lg" loading="lazy" />
        </div>
      </div>
      {dom.alt ? <figcaption className="mt-3 text-center text-sm text-white/45">{dom.alt}</figcaption> : null}
    </figure>
  );
}

function BookCallout(props: { children?: ReactNode } & RemarkNodeProp) {
  const { children } = stripRemarkNode(props) as { children?: ReactNode };
  const text = flattenText(children).trim();
  const tip = /^Tip\b/.test(text);
  const note = /^Note\b/.test(text);
  if (tip) {
    return (
      <aside className="not-prose my-10 rounded-2xl border border-aqua/25 bg-gradient-to-br from-aqua/[0.12] via-white/[0.03] to-transparent p-5 shadow-card sm:p-6">
        <div className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-aqua">
          <span className="inline-block h-2 w-2 rounded-full bg-aqua shadow-[0_0_12px_rgba(207,169,107,0.7)]" />
          Callout
        </div>
        <div className="book-callout-inner text-[0.95rem] leading-relaxed text-white/85 [&_a]:font-semibold [&_a]:text-aqua [&_strong]:text-white">
          {children}
        </div>
      </aside>
    );
  }
  if (note) {
    return (
      <aside className="not-prose my-10 rounded-2xl border border-lilac/25 bg-gradient-to-br from-lilac/[0.12] via-white/[0.03] to-transparent p-5 shadow-card sm:p-6">
        <div className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-lilac">
          <span className="inline-block h-2 w-2 rounded-full bg-lilac shadow-[0_0_12px_rgba(179,136,255,0.55)]" />
          Field note
        </div>
        <div className="book-callout-inner text-[0.95rem] leading-relaxed text-white/85 [&_a]:font-semibold [&_a]:text-aqua [&_strong]:text-white">
          {children}
        </div>
      </aside>
    );
  }
  return (
    <blockquote className="not-prose my-8 border-l-4 border-white/20 bg-white/[0.03] py-2 pl-5 text-white/75">
      {children}
    </blockquote>
  );
}

function BookHr() {
  return (
    <div className="not-prose my-16 flex items-center gap-4" role="separator">
      <div className="h-px flex-1 bg-gradient-to-r from-transparent via-coral/45 to-transparent" />
      <div className="flex h-9 w-9 items-center justify-center rounded-full border border-white/15 bg-white/[0.06]">
        <span className="block h-2 w-2 rotate-45 bg-gradient-to-br from-aqua to-lilac opacity-80" />
      </div>
      <div className="h-px flex-1 bg-gradient-to-r from-transparent via-lilac/45 to-transparent" />
    </div>
  );
}

function BookLink(props: React.AnchorHTMLAttributes<HTMLAnchorElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  const href = typeof dom.href === "string" ? dom.href : "";
  if (href.startsWith("/")) {
    return (
      <Link href={href} className={cn("font-semibold text-aqua underline-offset-2 hover:underline", dom.className)}>
        {dom.children}
      </Link>
    );
  }
  return (
    <a
      {...dom}
      className={cn("font-semibold text-aqua underline-offset-2 hover:underline", dom.className)}
      target="_blank"
      rel="noopener noreferrer"
    />
  );
}

function BookTable(props: React.HTMLAttributes<HTMLTableElement> & RemarkNodeProp) {
  const { children, ...rest } = stripRemarkNode(props);
  return (
    <div className="not-prose my-12 overflow-x-auto rounded-2xl border border-white/12 bg-black/30 p-1 shadow-inner">
      <table {...rest} className="min-w-full border-collapse text-left text-sm text-white/85">
        {children}
      </table>
    </div>
  );
}

function BookThead(props: React.HTMLAttributes<HTMLTableSectionElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  return <thead {...dom} className={cn("bg-white/[0.06] text-xs uppercase tracking-wider text-aqua/90", dom.className)} />;
}

function BookTbody(props: React.HTMLAttributes<HTMLTableSectionElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  return <tbody {...dom} className={cn("[&_tr:nth-child(odd)]:bg-white/[0.03]", dom.className)} />;
}

function BookTh(props: React.HTMLAttributes<HTMLTableCellElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  return <th {...dom} className={cn("border-b border-white/15 px-4 py-3 font-semibold", dom.className)} />;
}

function BookTd(props: React.HTMLAttributes<HTMLTableCellElement> & RemarkNodeProp) {
  const dom = stripRemarkNode(props);
  return <td {...dom} className={cn("border-b border-white/10 px-4 py-3 align-top text-white/75", dom.className)} />;
}

const bookComponents: Partial<Components> = {
  h2: BookH2,
  h3: BookH3,
  img: BookImg,
  blockquote: BookCallout,
  hr: BookHr,
  a: BookLink,
  table: BookTable,
  thead: BookThead,
  tbody: BookTbody,
  th: BookTh,
  td: BookTd,
};

export function BookMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]} components={bookComponents}>
      {content}
    </ReactMarkdown>
  );
}
