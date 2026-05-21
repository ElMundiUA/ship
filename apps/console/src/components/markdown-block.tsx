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

import type { Components } from "react-markdown";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const REMARK_PLUGINS = [remarkGfm];

type MarkdownBlockProps = {
  children: string;
  /** Optional className applied to the wrapping ``<div>``. */
  className?: string;
  /**
   * When set, everything from the first markdown heading whose text
   * matches one of these (case-insensitive) is split off and rendered
   * inside a collapsed ``<details>`` — keeps inbox letters short by
   * default, with the forensic audit trail one click away. No effect
   * when no matching heading exists. We do the split on the raw string
   * (no ``rehype-raw`` dependency / HTML-injection surface).
   */
  collapseFromHeadings?: string[];
};

// Shared element styling. ``react-markdown`` calls these for every node
// of the matching type.
const COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="font-display text-lg font-bold text-white">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-5 font-display text-base font-bold text-white first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-4 font-display text-sm font-bold text-white first:mt-0">
      {children}
    </h3>
  ),
  p: ({ children }) => <p className="text-white/70">{children}</p>,
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-block max-w-[22rem] truncate align-bottom text-aqua hover:underline"
    >
      {children}
    </a>
  ),
  ul: ({ children }) => (
    <ul className="list-disc space-y-1.5 pl-5">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal space-y-1.5 pl-5">{children}</ol>
  ),
  li: ({ children }) => <li className="text-white/70">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-aqua/50 pl-4 text-white/65">
      {children}
    </blockquote>
  ),
  // Tables had no overrides, so the digest's Throughput table fell back
  // to raw browser defaults (no padding, no alignment) — the "messy
  // table" the operator flagged. Thin dividers, tabular numerals, and
  // right-aligned numeric columns (everything but the first).
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-collapse text-[13px] tabular-nums">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="border-b border-white/15">{children}</thead>
  ),
  th: ({ children }) => (
    <th className="px-3 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wide text-white/55 [&:not(:first-child)]:text-right">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-t border-white/[0.06] px-3 py-1.5 text-white/80 [&:not(:first-child)]:text-right">
      {children}
    </td>
  ),
  // De-emphasised: agent letters are dense with inline tokens; a loud
  // chip on every id/path drowns the prose.
  code: ({ children }) => (
    <code className="rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11px] text-white/75">
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
};

function renderMarkdown(md: string): ReactNode {
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={COMPONENTS}>
      {md}
    </ReactMarkdown>
  );
}

/** Split ``md`` at the first heading line matching one of ``headings``
 * (case-insensitive). Returns ``[head, tail]`` where ``tail`` is null
 * when no match. Tail keeps its heading line so the disclosure can show
 * it as a label. */
function splitAtHeading(
  md: string,
  headings: string[],
): [string, string | null] {
  const wanted = headings.map((h) => h.trim().toLowerCase());
  const lines = md.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const m = /^#{1,6}\s+(.*)$/.exec(lines[i]);
    if (m && wanted.includes(m[1].trim().toLowerCase())) {
      return [lines.slice(0, i).join("\n").trim(), lines.slice(i).join("\n").trim()];
    }
  }
  return [md, null];
}

/**
 * Render ``children`` as GitHub-flavoured markdown with the Console's
 * default text/heading/code styles. Returns ``null`` for empty input
 * so the caller can short-circuit at the call site without an extra
 * ternary.
 */
export function MarkdownBlock({
  children,
  className,
  collapseFromHeadings,
}: MarkdownBlockProps): ReactNode {
  if (!children || !children.trim()) return null;

  const wrap = `space-y-3 text-sm leading-relaxed text-white/80 ${className ?? ""}`.trim();

  if (collapseFromHeadings && collapseFromHeadings.length > 0) {
    const [head, tail] = splitAtHeading(children, collapseFromHeadings);
    if (tail) {
      return (
        <div className={wrap}>
          {head ? renderMarkdown(head) : null}
          <details className="group/details mt-2">
            <summary className="flex cursor-pointer items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.16em] text-white/35 hover:text-white/60">
              <span className="transition-transform group-open/details:rotate-90">
                ▸
              </span>
              Technical details
            </summary>
            <div className="mt-2 border-l border-white/10 pl-3 text-white/55">
              {renderMarkdown(tail)}
            </div>
          </details>
        </div>
      );
    }
  }

  return <div className={wrap}>{renderMarkdown(children)}</div>;
}
