"use client";

/**
 * Chat markdown renderer.
 *
 * The navigator streams model output as markdown from the first
 * delta — we render it through ``react-markdown`` throughout the
 * stream (not just after the turn ends) so the user doesn't see a
 * jarring "plain text → formatted text" flip at the end.
 *
 * The renderer is aware of two UI directives the agent can emit as
 * fenced code blocks:
 *
 * - ``ship-choice`` — a multiple-choice card the user can click.
 *   The fenced body is JSON: ``{"prompt": "...", "options": ["A",
 *   "B"]}``. Clicking an option sends the chosen label as the next
 *   user message via :func:`ChoiceContext`.
 * - ``ship-todo`` — a task-list card. Body JSON: ``{"title": "...",
 *   "items": [{"text": "...", "status": "done"|"in_progress"|
 *   "pending"}]}``.
 *
 * Both directives render as styled cards inside the assistant
 * turn. During streaming, partial JSON is tolerated — we try to
 * parse and, on failure, fall back to a neutral "preparing…" state
 * until the turn completes.
 */

import { createContext, useContext, type ReactNode } from "react";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// ---------------------------------------------------------------------------
// Context for interactive directives
// ---------------------------------------------------------------------------

type ChoiceHandler = (label: string) => void;

const ChoiceContext = createContext<ChoiceHandler | null>(null);

export function ChoiceProvider({
  onChoose,
  children,
}: {
  onChoose: ChoiceHandler;
  children: ReactNode;
}) {
  return (
    <ChoiceContext.Provider value={onChoose}>{children}</ChoiceContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Word-by-word fade helper
// ---------------------------------------------------------------------------

/**
 * Split plain text into React spans so each word (whitespace-bounded
 * token) can carry its own mount animation. React reconciles spans
 * by positional index, so previously-rendered words don't replay
 * their animation on every delta — only the *new* trailing words do.
 *
 * Returns ``children`` unchanged when ``animate`` is false so static
 * (historical) transcripts don't re-animate after a reload.
 */
function renderTextNodes(children: ReactNode, animate: boolean): ReactNode {
  if (!animate) return children;
  return mapChildrenText(children);
}

function mapChildrenText(node: ReactNode): ReactNode {
  if (typeof node === "string") {
    return splitIntoWordSpans(node);
  }
  if (Array.isArray(node)) {
    return node.map((n, i) => (
      <span key={i} style={{ display: "contents" }}>
        {mapChildrenText(n)}
      </span>
    ));
  }
  return node;
}

function splitIntoWordSpans(text: string): ReactNode {
  const parts = text.match(/\S+|\s+/g);
  if (!parts) return text;
  return parts.map((p, i) => {
    if (/^\s+$/.test(p)) return p;
    return (
      <span key={i} className="chat-word">
        {p}
      </span>
    );
  });
}

// ---------------------------------------------------------------------------
// Fenced-block directive parser
// ---------------------------------------------------------------------------

type DirectiveKind = "ship-choice" | "ship-todo" | null;

function parseDirective(className: string | undefined): DirectiveKind {
  if (!className) return null;
  const match = /language-(\S+)/.exec(className);
  if (!match) return null;
  const lang = match[1].toLowerCase();
  if (lang === "ship-choice" || lang === "ship-todo") return lang;
  return null;
}

function tryParseJSON(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // Stream not complete yet — caller shows a placeholder.
  }
  return null;
}

function ChoiceCard({ raw }: { raw: string }) {
  const onChoose = useContext(ChoiceContext);
  const parsed = tryParseJSON(raw);
  if (!parsed) {
    return <DirectivePlaceholder label="Preparing options…" />;
  }
  const prompt = typeof parsed.prompt === "string" ? parsed.prompt : null;
  const rawOptions = Array.isArray(parsed.options) ? parsed.options : [];
  const options = rawOptions
    .map((o) => {
      if (typeof o === "string") return { label: o, value: o };
      if (o && typeof o === "object") {
        const obj = o as Record<string, unknown>;
        const label = typeof obj.label === "string" ? obj.label : null;
        const value = typeof obj.value === "string" ? obj.value : label;
        return label ? { label, value: value ?? label } : null;
      }
      return null;
    })
    .filter((o): o is { label: string; value: string } => !!o);

  return (
    <div className="my-3 rounded-2xl border border-lilac/30 bg-lilac/[0.05] p-4 last:mb-0">
      {prompt ? (
        <p className="mb-3 text-[13px] font-semibold text-white/90">{prompt}</p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {options.length === 0 ? (
          <span className="text-[12px] text-white/50">No options offered.</span>
        ) : (
          options.map((opt) => (
            <button
              key={opt.value}
              type="button"
              disabled={!onChoose}
              onClick={() => onChoose?.(opt.value)}
              className="inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-3.5 py-1.5 text-[12px] font-semibold text-white/85 transition hover:border-lilac/50 hover:bg-lilac/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {opt.label}
            </button>
          ))
        )}
      </div>
    </div>
  );
}

function TodoCard({ raw }: { raw: string }) {
  const parsed = tryParseJSON(raw);
  if (!parsed) {
    return <DirectivePlaceholder label="Preparing plan…" />;
  }
  const title = typeof parsed.title === "string" ? parsed.title : null;
  const items = Array.isArray(parsed.items) ? parsed.items : [];
  const normalized = items
    .map((item) => {
      if (typeof item === "string") {
        return { text: item, status: "pending" as const };
      }
      if (item && typeof item === "object") {
        const obj = item as Record<string, unknown>;
        const text = typeof obj.text === "string" ? obj.text : null;
        const rawStatus = typeof obj.status === "string" ? obj.status : "pending";
        const status: "done" | "in_progress" | "pending" =
          rawStatus === "done" || rawStatus === "in_progress"
            ? rawStatus
            : "pending";
        return text ? { text, status } : null;
      }
      return null;
    })
    .filter(
      (i): i is { text: string; status: "done" | "in_progress" | "pending" } =>
        !!i,
    );

  return (
    <div className="my-3 rounded-2xl border border-aqua/25 bg-aqua/[0.04] p-4 last:mb-0">
      <div className="mb-3 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.22em] text-aqua/80">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-aqua" />
        plan
      </div>
      {title ? (
        <p className="mb-3 text-[13px] font-semibold text-white/90">{title}</p>
      ) : null}
      <ul className="space-y-1.5">
        {normalized.map((item, i) => (
          <li key={i} className="flex items-start gap-2.5 text-[13px]">
            <TodoStatusIcon status={item.status} />
            <span
              className={
                item.status === "done"
                  ? "text-white/45 line-through"
                  : item.status === "in_progress"
                    ? "text-white"
                    : "text-white/80"
              }
            >
              {item.text}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TodoStatusIcon({
  status,
}: {
  status: "done" | "in_progress" | "pending";
}) {
  if (status === "done") {
    return (
      <span className="mt-[3px] grid h-4 w-4 shrink-0 place-items-center rounded-full border border-emerald-400/50 bg-emerald-400/15 text-[10px] text-emerald-300">
        ✓
      </span>
    );
  }
  if (status === "in_progress") {
    return (
      <span className="relative mt-[3px] grid h-4 w-4 shrink-0 place-items-center rounded-full border border-aqua/50 bg-aqua/15">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-aqua" />
      </span>
    );
  }
  return (
    <span className="mt-[3px] h-4 w-4 shrink-0 rounded-full border border-white/20" />
  );
}

function DirectivePlaceholder({ label }: { label: string }) {
  return (
    <div className="my-3 rounded-2xl border border-white/10 bg-white/[0.02] p-4 last:mb-0">
      <span className="chat-shimmer text-[12px] font-semibold uppercase tracking-[0.22em]">
        {label}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReactMarkdown component overrides
// ---------------------------------------------------------------------------

function buildComponents(animate: boolean): Components {
  const W = (children: ReactNode) => renderTextNodes(children, animate);
  return {
    h1: ({ children }) => (
      <h1 className="mb-2 mt-4 text-lg font-semibold text-white first:mt-0">
        {W(children)}
      </h1>
    ),
    h2: ({ children }) => (
      <h2 className="mb-2 mt-4 text-base font-semibold text-white first:mt-0">
        {W(children)}
      </h2>
    ),
    h3: ({ children }) => (
      <h3 className="mb-1.5 mt-3 text-sm font-semibold text-white/95 first:mt-0">
        {W(children)}
      </h3>
    ),
    p: ({ children }) => (
      <p className="mb-3 text-white/90 last:mb-0 [&+p]:mt-0">{W(children)}</p>
    ),
    ul: ({ children }) => (
      <ul className="mb-3 list-disc space-y-1 pl-5 text-white/90 last:mb-0">
        {children}
      </ul>
    ),
    ol: ({ children }) => (
      <ol className="mb-3 list-decimal space-y-1 pl-5 text-white/90 last:mb-0">
        {children}
      </ol>
    ),
    li: ({ children }) => (
      <li className="leading-relaxed">{W(children)}</li>
    ),
    strong: ({ children }) => (
      <strong className="font-semibold text-white">{W(children)}</strong>
    ),
    em: ({ children }) => (
      <em className="italic text-white/85">{W(children)}</em>
    ),
    a: ({ href, children }) => (
      <a
        href={href}
        className="text-aqua underline-offset-2 hover:underline"
        target="_blank"
        rel="noopener noreferrer"
      >
        {W(children)}
      </a>
    ),
    hr: () => <hr className="my-4 border-white/15" />,
    blockquote: ({ children }) => (
      <blockquote className="mb-3 border-l-2 border-lilac/50 pl-3 text-white/75">
        {children}
      </blockquote>
    ),
    code: ({ className, children, ...props }) => {
      const directive = parseDirective(className);
      const raw = typeof children === "string"
        ? children
        : Array.isArray(children)
          ? children.filter((c) => typeof c === "string").join("")
          : "";
      if (directive === "ship-choice") {
        return <ChoiceCard raw={raw} />;
      }
      if (directive === "ship-todo") {
        return <TodoCard raw={raw} />;
      }
      const inline = !className;
      if (inline) {
        return (
          <code
            className="rounded bg-white/10 px-1 py-0.5 text-[0.92em] text-lilac/90"
            {...props}
          >
            {children}
          </code>
        );
      }
      return (
        <code className={className} {...props}>
          {children}
        </code>
      );
    },
    pre: ({ children }) => {
      // When a <pre> wraps one of our directive <code> blocks, unwrap
      // it so the interactive card doesn't inherit the code-block frame.
      if (isDirectivePre(children)) {
        return <>{children}</>;
      }
      return (
        <pre className="mb-3 overflow-x-auto rounded-md bg-black/45 p-3 text-[13px] text-white/88 last:mb-0">
          {children}
        </pre>
      );
    },
    table: ({ children }) => (
      <div className="my-3 overflow-x-auto last:mb-0">
        <table className="min-w-full border-collapse border border-white/15 text-[13px]">
          {children}
        </table>
      </div>
    ),
    thead: ({ children }) => <thead>{children}</thead>,
    tbody: ({ children }) => <tbody>{children}</tbody>,
    tr: ({ children }) => <tr className="even:bg-white/[0.03]">{children}</tr>,
    th: ({ children }) => (
      <th className="border border-white/15 bg-white/[0.06] px-2.5 py-2 text-left font-semibold text-white/90">
        {children}
      </th>
    ),
    td: ({ children }) => (
      <td className="border border-white/15 px-2.5 py-2 align-top text-white/85">
        {children}
      </td>
    ),
  };
}

function isDirectivePre(children: ReactNode): boolean {
  // Children of <pre> from react-markdown is typically a single <code>
  // React element. Inspect its className to detect our directives.
  if (!children) return false;
  const arr = Array.isArray(children) ? children : [children];
  for (const child of arr) {
    if (
      child &&
      typeof child === "object" &&
      "props" in child &&
      child.props &&
      typeof child.props === "object"
    ) {
      const cls = (child.props as { className?: string }).className;
      if (parseDirective(cls) !== null) return true;
    }
  }
  return false;
}

export function ChatMarkdown({
  text,
  animate = false,
}: {
  text: string;
  /** Animate newly-rendered word tokens with a short fade-in. */
  animate?: boolean;
}) {
  const components = buildComponents(animate);
  return (
    <div className="chat-md text-[14px] leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
