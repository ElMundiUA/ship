"use client";

/**
 * Single-window chat client for the C12 agent.
 *
 * UX notes (see parent PR for motivation):
 *
 * - **Scroll discipline.** When the user submits, we smoothly scroll
 *   their fresh message to the top of the viewport so the reply
 *   appears below. While the reply streams we deliberately do *not*
 *   auto-scroll — the user can read from where they are. Autoscroll
 *   only kicks back in if they're already near the bottom.
 * - **Word-by-word fade reveal.** As deltas arrive we commit entire
 *   words to the rendered output on a fast timer. Each newly
 *   committed word fades in via the ``.chat-word`` CSS animation.
 *   Markdown is applied from the first delta — the user never sees
 *   a "plain text → formatted text" flip.
 * - **Stateful animation.** Only messages born in this session
 *   animate. Historical messages loaded from the server render
 *   statically, so reloading the page doesn't replay the last
 *   reply's reveal.
 * - **Thinking / tool cards.** Between the user's submit and the
 *   first delta, an animated "Thinking…" card holds the space so
 *   the page doesn't feel frozen. Tool calls render as styled
 *   pill-cards with a running shimmer while they're in flight.
 *
 * The backend event protocol is unchanged (``thread`` /
 * ``user_message`` / ``topic_shift`` / ``delta`` / ``tool_call`` /
 * ``tool_result`` / ``assistant_message`` / ``end`` / ``error``).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { ChatMarkdown, ChoiceProvider } from "./chat-markdown";

type Role = "user" | "assistant" | "system" | "tool";

type Message = {
  id: string;
  role: Role;
  body: string;
  meta?: Record<string, unknown>;
  createdAt?: string;
  /** Client-side accumulator flag — set while the assistant is streaming its reply. */
  streaming?: boolean;
};

type Thread = {
  id: string;
  title: string;
  status: "active" | "archived";
  topic_summary: string | null;
  packed_into_bucket_id: string | null;
  created_at: string;
  updated_at: string;
};

type ToolCallRow = {
  id: string;
  name: string;
  args: Record<string, unknown>;
  /** Populated when the matching ``tool_result`` event arrives. */
  result?: { ok: boolean; result?: unknown; error?: string };
};

type TopicShift = {
  suggested_bucket_name: string | null;
  confidence: number;
  reason: string | null;
};

type StreamEvent =
  | { type: "thread"; thread: Thread }
  | { type: "user_message"; message: Message }
  | { type: "topic_shift"; shift: TopicShift }
  | { type: "delta"; text: string }
  | {
      type: "tool_call";
      id: string;
      name: string;
      args?: Record<string, unknown>;
      arguments?: Record<string, unknown>;
    }
  | {
      type: "tool_result";
      id: string;
      name?: string;
      output?: string;
      ok?: boolean;
      result?: unknown;
      error?: string;
    }
  | { type: "assistant_message"; message: Message }
  | { type: "end"; reason: string }
  | { type: "error"; detail?: string; error?: string };

type InitialState = {
  workspaceId: string;
  thread: Thread & { messages: Message[] };
};

function clientId(): string {
  return "c_" + Math.random().toString(36).slice(2, 10);
}

// Reveal cadence for the word-by-word fade. We commit at most one
// word per tick, so the text feels "typed" rather than "pasted".
// Cadence is adaptive — if the buffer grows faster than we reveal,
// we speed up so the render doesn't trail the stream.
const REVEAL_MIN_DELAY = 18;
const REVEAL_FAR_DELAY = 35;

export function SingleWindowChat({ workspaceId, thread }: InitialState) {
  const [current, setCurrent] = useState<Thread>(thread);
  const [messages, setMessages] = useState<Message[]>(thread.messages);
  const [tools, setTools] = useState<ToolCallRow[]>([]);
  const [shift, setShift] = useState<TopicShift | null>(null);
  const [streaming, setStreaming] = useState(false);
  // Distinguish "user submitted, nothing back yet" from "agent
  // started streaming text". Drives the Thinking card.
  const [awaitingFirstDelta, setAwaitingFirstDelta] = useState(false);
  const [draft, setDraft] = useState("");
  const [errorText, setErrorText] = useState<string | null>(null);

  // Per-assistant-message reveal progress (prefix length). Only
  // the currently-streaming message advances over time; finalized
  // messages sit at ``body.length`` and never re-animate. Held in
  // React state so the reveal tick triggers re-renders.
  const [revealed, setRevealed] = useState<Record<string, number>>({});
  const streamingIdRef = useRef<string | null>(null);

  // Messages created during this mount get their ids added here so
  // ``MessageRow`` knows to animate them. Historical messages from
  // the initial server payload are *not* in this set, so a page
  // reload doesn't replay the last reply's fade-in.
  const animatedIdsRef = useRef<Set<string>>(new Set());

  // Reserved empty space below the last message while a reply is
  // in flight. Without it the scroller has no room to scroll the
  // fresh user message to the top of the viewport — ``scrollTo``
  // silently no-ops because ``scrollHeight === clientHeight``. We
  // size it to the scroller's visible height on submit and release
  // it back to zero when the stream ends. This mirrors how ChatGPT
  // keeps the user prompt pinned at the top while the reply grows
  // in beneath it.
  const [bottomSpacerPx, setBottomSpacerPx] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const lastUserAnchorRef = useRef<HTMLDivElement | null>(null);

  // Word-by-word reveal tick. Only the currently-streaming message
  // advances — everything else sits at full length. We step one
  // word per tick so bursts of delta chars don't dump all at once.
  useEffect(() => {
    const id = streamingIdRef.current;
    if (!id) return;
    const streamingMsg = messages.find((m) => m.id === id);
    if (!streamingMsg) return;
    const cur = revealed[id] ?? 0;
    if (cur >= streamingMsg.body.length) return;
    const idx = findNextWordBoundary(streamingMsg.body, cur);
    const nextLen = idx < 0 ? streamingMsg.body.length : idx;
    const remaining = streamingMsg.body.length - cur;
    // When the backend has already emitted the full body (end
    // event fired, ``streaming`` flag cleared) we fast-forward so
    // the UI doesn't trail behind for several seconds after the
    // model stopped talking.
    const streamDone = !streamingMsg.streaming;
    const delay = streamDone
      ? 6
      : remaining > 300
        ? REVEAL_MIN_DELAY
        : REVEAL_FAR_DELAY;
    const h = window.setTimeout(() => {
      setRevealed((prev) => ({ ...prev, [id]: nextLen }));
    }, delay);
    return () => window.clearTimeout(h);
  }, [messages, revealed]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const handleEvent = useCallback((evt: StreamEvent) => {
    switch (evt.type) {
      case "thread": {
        setCurrent(evt.thread);
        return;
      }
      case "user_message": {
        setMessages((prev) => {
          const trimmed = prev.filter(
            (m) => !(m.role === "user" && m.id.startsWith("c_")),
          );
          return [...trimmed, evt.message];
        });
        // Fresh turn — any in-flight assistant reveal is done; the
        // streaming slot is free until the first delta lands.
        streamingIdRef.current = null;
        return;
      }
      case "topic_shift": {
        setShift(evt.shift);
        return;
      }
      case "delta": {
        setAwaitingFirstDelta(false);
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.role === "assistant" && last.streaming) {
            const next = prev.slice(0, -1);
            next.push({ ...last, body: last.body + evt.text });
            return next;
          }
          const fresh: Message = {
            id: clientId(),
            role: "assistant",
            body: evt.text,
            streaming: true,
          };
          streamingIdRef.current = fresh.id;
          animatedIdsRef.current.add(fresh.id);
          // Seed the reveal at 0 so the first word fades in
          // rather than popping in wholesale.
          setRevealed((prev) => ({ ...prev, [fresh.id]: 0 }));
          return [...prev, fresh];
        });
        return;
      }
      case "tool_call": {
        const args =
          evt.args ?? evt.arguments ?? ({} as Record<string, unknown>);
        setTools((prev) => [
          ...prev,
          { id: evt.id, name: evt.name, args },
        ]);
        return;
      }
      case "tool_result": {
        const normalized = normalizeToolResult(evt);
        setTools((prev) =>
          prev.map((t) =>
            t.id === evt.id ? { ...t, result: normalized } : t,
          ),
        );
        return;
      }
      case "assistant_message": {
        setMessages((prev) => {
          const idx = findLastIndex(
            prev,
            (m) => m.role === "assistant" && !!m.streaming,
          );
          if (idx < 0) {
            // Server-only finalize (no streaming placeholder) —
            // still mark as animated since this is a fresh turn.
            animatedIdsRef.current.add(evt.message.id);
            return [...prev, { ...evt.message, streaming: true }];
          }
          const next = prev.slice();
          const existing = next[idx];
          // Keep the client id stable across finalization so the
          // React key doesn't change — if we swapped to the server
          // id here, the whole row would re-mount and the reveal
          // animation would re-play from scratch.
          next[idx] = {
            ...existing,
            body: evt.message.body,
            meta: { ...(evt.message.meta ?? {}), server_id: evt.message.id },
          };
          return next;
        });
        return;
      }
      case "end": {
        setStreaming(false);
        setAwaitingFirstDelta(false);
        // Finalize: drop the streaming flag on whichever assistant
        // row was live, so new tool calls in a *future* turn don't
        // re-target the same row. We intentionally do NOT touch
        // ``bottomSpacerPx`` here — collapsing it at ``end`` would
        // yank the scroller's runway out from under the reader
        // and the pinned user prompt would snap back down to the
        // bottom of the viewport. The spacer is only reset on the
        // next ``send`` / ``resetConversation``.
        setMessages((prev) => {
          const idx = findLastIndex(
            prev,
            (m) => m.role === "assistant" && !!m.streaming,
          );
          if (idx < 0) return prev;
          const next = prev.slice();
          next[idx] = { ...next[idx], streaming: false };
          return next;
        });
        return;
      }
      case "error": {
        setErrorText(evt.detail ?? evt.error ?? "Agent error");
        setStreaming(false);
        setAwaitingFirstDelta(false);
        // Same rationale as ``end`` — don't yank the spacer out
        // from under the user when the turn errors.
        return;
      }
    }
  }, []);

  const send = useCallback(
    async (message: string, opts: { forceNewThread?: boolean } = {}) => {
      const trimmed = message.trim();
      if (!trimmed || streaming) return;
      setErrorText(null);
      setStreaming(true);
      setAwaitingFirstDelta(true);
      setTools([]);

      const optimisticId = clientId();
      const optimistic: Message = {
        id: optimisticId,
        role: "user",
        body: trimmed,
        streaming: false,
      };
      setMessages((prev) => [...prev, optimistic]);
      setDraft("");
      streamingIdRef.current = null;

      // Smoothly scroll the fresh user message to (close to) the
      // top of the viewport so the upcoming reply has room to
      // render below without jumping the layout. We first reserve
      // an empty spacer equal to the viewport's height so the
      // scroller actually *has* runway to move into — otherwise
      // the user message sits at the bottom of the list and
      // ``scrollTo`` silently no-ops. Two RAFs: one for the DOM
      // to lay out the spacer, then the smooth scroll.
      const scroller = scrollerRef.current;
      if (scroller) {
        setBottomSpacerPx(Math.max(scroller.clientHeight - 96, 240));
      }
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const anchor = lastUserAnchorRef.current;
          const el = scrollerRef.current;
          if (!anchor || !el) return;
          // Use rect math instead of ``offsetTop`` so we don't
          // depend on the nearest positioned ancestor being the
          // scroller itself.
          const anchorTop = anchor.getBoundingClientRect().top;
          const elTop = el.getBoundingClientRect().top;
          const target = el.scrollTop + (anchorTop - elTop) - 16;
          el.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
        });
      });

      const ac = new AbortController();
      abortRef.current = ac;

      try {
        const res = await fetch("/api/chat/stream", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            workspace_id: workspaceId,
            message: trimmed,
            force_new_thread: !!opts.forceNewThread,
          }),
          signal: ac.signal,
        });

        if (!res.ok || !res.body) {
          const txt = await res.text().catch(() => "");
          throw new Error(
            txt && txt.length > 0
              ? `${res.status}: ${txt.slice(0, 200)}`
              : `HTTP ${res.status}`,
          );
        }

        await consumeSSE(res.body, handleEvent);
      } catch (err) {
        if ((err as { name?: string }).name === "AbortError") return;
        setErrorText(
          err instanceof Error ? err.message : "Unknown streaming error",
        );
      } finally {
        setStreaming(false);
        setAwaitingFirstDelta(false);
      }
    },
    [handleEvent, streaming, workspaceId],
  );

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    void send(draft);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      void send(draft);
    }
  };

  const resetConversation = useCallback(
    async (opts: { bucketName?: string } = {}) => {
      if (streaming) abortRef.current?.abort();
      setErrorText(null);
      const res = await fetch("/api/chat/new-thread", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspace_id: workspaceId,
          pack_into_bucket_name: opts.bucketName ?? null,
        }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        setErrorText(
          txt && txt.length > 0
            ? `${res.status}: ${txt.slice(0, 200)}`
            : `HTTP ${res.status}`,
        );
        return;
      }
      const fresh = (await res.json()) as Thread & { messages: Message[] };
      setCurrent(fresh);
      setMessages(fresh.messages ?? []);
      setTools([]);
      setShift(null);
      streamingIdRef.current = null;
      setRevealed({});
      animatedIdsRef.current = new Set();
      setBottomSpacerPx(0);
    },
    [streaming, workspaceId],
  );

  const visibleMessages = useMemo(
    () => messages.filter((m) => m.role !== "system"),
    [messages],
  );
  const lastUserIndex = useMemo(() => {
    for (let i = visibleMessages.length - 1; i >= 0; i--) {
      if (visibleMessages[i].role === "user") return i;
    }
    return -1;
  }, [visibleMessages]);
  const hasStreamingAssistant = useMemo(
    () => visibleMessages.some((m) => m.role === "assistant" && !!m.streaming),
    [visibleMessages],
  );
  // Single-line status that sits at the *end* of the turn (below
  // whatever the agent has streamed so far, or below the user
  // prompt if the agent hasn't started talking yet). It replaces
  // itself as the turn progresses — no stacking:
  //
  //   Thinking…                    ← after send, before any output
  //   ↓ agent calls tool A
  //   Calling A…                   ← overrides Thinking
  //   ↓ tool A resolves, no next tool yet, no streamed text yet
  //   Thinking…
  //   ↓ agent starts streaming prose
  //   (status line disappears — the streaming text *is* the signal)
  //   ↓ agent calls tool B mid-stream
  //   Calling B…                   ← appears *below* the streamed text
  //   ↓ tool B resolves, stream resumes
  //   (status line disappears again)
  //   ↓ turn ends
  //   (status line gone)
  //
  // Key: a currently-running tool *always* shows its own status,
  // even while the assistant is also streaming text — modern
  // agents interleave delta + tool_call events, and without this
  // the user has no feedback that a tool is in flight between
  // prose paragraphs.
  const turnStatus = useMemo((): {
    tone: "shimmer" | "error";
    text: string;
  } | null => {
    if (!streaming) return null;
    const latest = tools.length > 0 ? tools[tools.length - 1] : null;
    if (latest && !latest.result) {
      return {
        tone: "shimmer",
        text: `Calling ${prettyToolName(latest.name)}…`,
      };
    }
    if (latest && latest.result && !latest.result.ok) {
      return {
        tone: "error",
        text: `${prettyToolName(latest.name)} — ${latest.result.error ?? "failed"}`,
      };
    }
    // No running / errored tool at the head of the queue. Only
    // fill the slot with "Thinking…" if the assistant hasn't
    // started streaming text yet — once prose is flowing, the
    // prose itself is the progress indicator and an extra
    // "Thinking…" line would just be visual noise.
    if (!hasStreamingAssistant && awaitingFirstDelta) {
      return { tone: "shimmer", text: "Thinking…" };
    }
    return null;
  }, [streaming, hasStreamingAssistant, tools, awaitingFirstDelta]);

  // Map each visible message to how many characters we should
  // render right now. Non-streaming rows (historical + finalized
  // past turns) always show their full body; only the currently
  // active assistant slot is clamped by the reveal tick.
  const revealMap = useMemo(() => {
    const map = new Map<string, number>();
    for (const m of visibleMessages) {
      if (m.id === streamingIdRef.current) {
        const cur = revealed[m.id] ?? 0;
        map.set(m.id, Math.min(cur, m.body.length));
      } else {
        map.set(m.id, m.body.length);
      }
    }
    return map;
  }, [visibleMessages, revealed]);

  return (
    <div className="flex h-[calc(100vh-12rem)] min-h-[34rem] flex-col">
      <div className="flex items-center gap-3 pb-2">
        <h2 className="text-sm font-semibold text-white/90">{current.title}</h2>
        <span className="text-[11px] text-white/35">
          {current.status === "active" ? "live" : "archived"} ·{" "}
          {visibleMessages.length} msg
        </span>
        <button
          type="button"
          onClick={() => resetConversation({})}
          className="ml-auto text-[11px] text-white/50 transition hover:text-white/90"
          disabled={streaming}
        >
          new conversation ↻
        </button>
      </div>

      {shift ? (
        <div className="flex items-start gap-3 py-2 text-[12px] text-amber-200/90">
          <span className="mt-0.5 h-1 w-1 shrink-0 rounded-full bg-amber-300" />
          <div className="flex-1">
            <strong className="font-semibold">Topic shift.</strong>{" "}
            {shift.reason ?? "Looks like you moved on."} Pack this thread into{" "}
            <code className="text-amber-100">
              {shift.suggested_bucket_name ?? "a new bucket"}
            </code>
            ?
            <button
              type="button"
              onClick={() =>
                resetConversation({
                  bucketName: shift.suggested_bucket_name ?? undefined,
                })
              }
              className="ml-2 font-semibold text-amber-100 underline-offset-2 hover:underline"
            >
              pack & start fresh
            </button>
            <button
              type="button"
              onClick={() => setShift(null)}
              className="ml-2 text-white/40 hover:text-white/80"
            >
              dismiss
            </button>
          </div>
        </div>
      ) : null}

      <div ref={scrollerRef} className="flex-1 overflow-y-auto py-4">
        <ChoiceProvider onChoose={(label) => void send(label)}>
          {visibleMessages.length === 0 ? (
            <EmptyHint />
          ) : (
            <div className="space-y-6">
              {visibleMessages.map((m, i) => {
                const isLastUser =
                  i === lastUserIndex && m.role === "user";
                return (
                  <MessageRow
                    key={m.id}
                    message={m}
                    animate={animatedIdsRef.current.has(m.id)}
                    revealLen={revealMap.get(m.id) ?? m.body.length}
                    anchorRef={isLastUser ? lastUserAnchorRef : null}
                  />
                );
              })}
              {/* Single-line turn status at the end of the turn
                  (below the streamed prose if there is any, below
                  the user prompt if not). Replaces itself as state
                  changes — never stacks. */}
              {turnStatus ? <TurnStatusLine {...turnStatus} /> : null}
              {/* Empty runway so the scroller can keep the fresh
                  user message pinned near the top of the viewport
                  while the reply streams in below. Shrinks back
                  to zero as soon as the turn ends. */}
              {bottomSpacerPx > 0 ? (
                <div
                  aria-hidden
                  style={{ height: bottomSpacerPx }}
                  className="pointer-events-none"
                />
              ) : null}
            </div>
          )}
        </ChoiceProvider>
      </div>

      {errorText ? (
        <div className="py-2 text-[12px] text-rose-300">{errorText}</div>
      ) : null}

      <form
        onSubmit={onSubmit}
        className="flex items-end gap-2 border-t border-white/5 pt-3"
      >
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask the agent. Enter to send, Shift-Enter for newline."
          rows={2}
          disabled={streaming}
          className="min-h-[48px] flex-1 resize-none bg-transparent px-1 py-2 text-sm text-white placeholder-white/25 focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={streaming || draft.trim().length === 0}
          className="text-sm font-semibold text-aqua transition hover:text-aqua/80 disabled:cursor-not-allowed disabled:text-white/25"
        >
          {streaming ? "…" : "send ↵"}
        </button>
      </form>
    </div>
  );
}

function EmptyHint() {
  return (
    <div className="flex h-full items-center justify-center text-center text-[12px] text-white/35">
      <div>
        <p className="font-semibold text-white/60">Navigator — single window.</p>
        <p className="mt-1 max-w-sm">
          Ask the agent anything about this workspace. It can search the
          repo knowledge base, read files, create tickets, and file
          feedback against artifacts.
        </p>
      </div>
    </div>
  );
}

function MessageRow({
  message,
  animate,
  revealLen,
  anchorRef,
}: {
  message: Message;
  animate: boolean;
  revealLen: number;
  anchorRef: React.RefObject<HTMLDivElement | null> | null;
}) {
  const isUser = message.role === "user";
  const label = isUser
    ? "You"
    : message.role === "assistant"
      ? "Ship"
      : message.role;
  const labelTint = isUser ? "text-aqua/80" : "text-lilac/80";

  const displayBody =
    message.role === "assistant"
      ? message.body.slice(0, Math.max(0, Math.min(revealLen, message.body.length)))
      : message.body;

  return (
    <div ref={anchorRef ?? undefined} className="text-[14px] leading-relaxed">
      <div
        className={`mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] ${labelTint}`}
      >
        {label}
      </div>
      <ChatMarkdown text={displayBody} animate={animate} />
    </div>
  );
}

function TurnStatusLine({
  tone,
  text,
}: {
  tone: "shimmer" | "error";
  text: string;
}) {
  const cls =
    tone === "shimmer"
      ? "chat-shimmer text-[13px]"
      : "text-[13px] text-rose-300/80";
  return <div className={cls}>{text}</div>;
}

function prettyToolName(name: string): string {
  return name.replace(/_/g, " ");
}

function normalizeToolResult(evt: {
  ok?: boolean;
  result?: unknown;
  error?: string;
  output?: string;
}): { ok: boolean; result?: unknown; error?: string } {
  if (
    typeof evt.ok === "boolean" &&
    (evt.error !== undefined || evt.result !== undefined)
  ) {
    return { ok: evt.ok, result: evt.result, error: evt.error };
  }
  const raw = evt.output;
  if (typeof raw !== "string") {
    return { ok: true, result: raw };
  }
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (
      parsed &&
      typeof parsed === "object" &&
      !Array.isArray(parsed) &&
      "error" in parsed
    ) {
      return {
        ok: false,
        error: String((parsed as { error?: unknown }).error ?? "failed"),
      };
    }
    return { ok: true, result: parsed };
  } catch {
    return { ok: true, result: raw };
  }
}

function findLastIndex<T>(arr: T[], pred: (t: T) => boolean): number {
  for (let i = arr.length - 1; i >= 0; i--) {
    if (pred(arr[i])) return i;
  }
  return -1;
}

function findNextWordBoundary(text: string, from: number): number {
  // Advance past the current token (word or whitespace run) so the
  // reveal lands on the next word boundary.
  if (from >= text.length) return -1;
  let i = from;
  const ws = /\s/.test(text[i]);
  while (i < text.length) {
    const cur = /\s/.test(text[i]);
    if (cur !== ws) break;
    i++;
  }
  // If we started in whitespace, advance one more word so the
  // next tick reveals an actual visible word, not just spaces.
  if (ws) {
    while (i < text.length && !/\s/.test(text[i])) i++;
  }
  return i;
}

// ---------------------------------------------------------------------------
// SSE parser
// ---------------------------------------------------------------------------

async function consumeSSE(
  body: ReadableStream<Uint8Array>,
  onEvent: (evt: StreamEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const parsed = parseFrame(frame);
      if (parsed) onEvent(parsed);
    }
  }
  if (buf.trim().length > 0) {
    const parsed = parseFrame(buf);
    if (parsed) onEvent(parsed);
  }
}

function parseFrame(frame: string): StreamEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
  } catch {
    return null;
  }
  return { type: event, ...payload } as unknown as StreamEvent;
}
