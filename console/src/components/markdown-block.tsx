/**
 * Shared markdown renderer for non-streaming surfaces (Inbox detail
 * card, Knowledge article body, etc.). The chat surface uses its own
 * ``chat-markdown.tsx`` because of streaming + interactive directives
 * (``ship-choice`` / ``ship-todo``); this component is the boring
 * read-only path: ``react-markdown`` + ``remark-gfm``, light Tailwind
 * styling that matches the existing Console palette.
 *
 * Use this anywhere we display agent-authored markdown the operator
 * should be able to read without raw ``##`` / ``**`` / ``1.`` syntax
 * showing through.
 */

import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const REMARK_PLUGINS = [remarkGfm];

type MarkdownBlockProps = {
  children: string;
  /** Optional className applied to the wrapping ``<div>``. */
  className?: string;
};

/**
 * Render ``children`` as GitHub-flavoured markdown with the Console's
 * default text/heading/code styles. Returns ``null`` for empty input
 * so the caller can short-circuit at the call site without an extra
 * ternary.
 */
export function MarkdownBlock({
  children,
  className,
}: MarkdownBlockProps): ReactNode {
  if (!children || !children.trim()) return null;
  return (
    <div className={`space-y-2 text-sm leading-relaxed text-white/80 ${className ?? ""}`.trim()}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        components={{
          h1: ({ children }) => (
            <h1 className="font-display text-lg font-bold text-white">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="font-display text-base font-bold text-white">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="font-display text-sm font-bold text-white">
              {children}
            </h3>
          ),
          p: ({ children }) => <p className="text-white/78">{children}</p>,
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="text-aqua hover:underline"
            >
              {children}
            </a>
          ),
          ul: ({ children }) => (
            <ul className="list-disc space-y-1 pl-5">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal space-y-1 pl-5">{children}</ol>
          ),
          li: ({ children }) => <li className="text-white/78">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-aqua/50 pl-4 text-white/65">
              {children}
            </blockquote>
          ),
          code: ({ children }) => (
            <code className="rounded bg-white/[0.08] px-1.5 py-0.5 font-mono text-[12px] text-aqua/95">
              {children}
            </code>
          ),
          pre: ({ children }) => (
            <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/35 p-3 text-xs text-white/85">
              {children}
            </pre>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-white">{children}</strong>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
