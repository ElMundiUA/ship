"use client";

/**
 * Single-window chat client for the C12 agent.
 *
 * The UX is deliberately "one conversation", not "list of chats":
 * there's always exactly one visible thread at a time. When the
 * user changes topic (or just asks for a fresh start), the current
 * thread gets packed into a named knowledge bucket and a new empty
 * thread replaces it in-place. Packed threads show up in the
 * sidebar as buckets — see ``buckets-sidebar.tsx``.
 *
 * The surface is deliberately flat: no message bubbles, no boxed
 * scroll panel, no tool-call "strip". Messages read top-to-bottom
 * like a transcript, with a thin role label above each turn. The
 * assistant's reply animates one character at a time via a
 * typewriter layer — the incoming SSE ``delta`` events accumulate
 * into the target string, and a local interval advances the
 * rendered length toward that target so the text feels *typed*
 * rather than dumped in bursts.
 *
 * The event protocol matches the backend exactly (``thread`` /
 * ``user_message`` / ``topic_shift`` / ``delta`` / ``tool_call`` /
 * ``tool_result`` / ``assistant_message`` / ``end`` / ``error``);
 * each type has a dedicated reducer that mutates the local
 * transcript state.
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

import { ChatMarkdown } from "./chat-markdown";

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
      /** Backend SSE field — same as ``args``. */
      args?: Record<string, unknown>;
      arguments?: Record<string, unknown>;
    }
  | {
      type: "tool_result";
      id: string;
      name?: string;
      /** Raw JSON string from the toolbox (success payload or ``{"error":...}``). */
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

export function SingleWindowChat({ workspaceId, thread }: InitialState) {
  const [current, setCurrent] = useState<Thread>(thread);
  const [messages, setMessages] = useState<Message[]>(thread.messages);
  const [tools, setTools] = useState<ToolCallRow[]>([]);
  const [shift, setShift] = useState<TopicShift | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [errorText, setErrorText] = useState<string | null>(null);

  // Typewriter cursor: how many characters of the *last* assistant
  // message are currently rendered. Bumped on a timer (see effect
  // below); reset to 0 whenever a fresh assistant turn starts.
  const [typedLen, setTypedLen] = useState(0);
  const currentAssistantIdRef = useRef<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  // Autoscroll to the newest content on every update. ``instant``
  // because the stream lands in tight bursts and smooth scroll
  // animations end up chasing their own tail.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, tools, streaming, typedLen]);

  useEffect(() => {
    // Always clean up the fetch abort controller if the component
    // unmounts mid-stream — the backend treats the dropped body as
    // "user went away" and stops generating.
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Typewriter tick. Speed is adaptive: when we're far behind the
  // target (big buffer of unreceived chars) we accelerate so the
  // cursor doesn't crawl for 10 seconds after the model already
  // finished. Near the tail we slow down for a natural "typing" feel.
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (!last || last.role !== "assistant") return;
    if (typedLen >= last.body.length) return;
    const remaining = last.body.length - typedLen;
    const step = remaining > 400 ? 6 : remaining > 120 ? 3 : remaining > 40 ? 2 : 1;
    const delay = remaining > 120 ? 10 : remaining > 40 ? 18 : 28;
    const h = window.setTimeout(() => {
      setTypedLen((n) => Math.min(last.body.length, n + step));
    }, delay);
    return () => window.clearTimeout(h);
  }, [messages, typedLen]);

  const handleEvent = useCallback(
    (evt: StreamEvent) => {
      switch (evt.type) {
        case "thread": {
          setCurrent(evt.thread);
          return;
        }
        case "user_message": {
          // The server-side user_message has the canonical id — replace
          // any optimistic placeholder we appended on submit.
          setMessages((prev) => {
            const trimmed = prev.filter(
              (m) => !(m.role === "user" && m.id.startsWith("c_")),
            );
            return [...trimmed, evt.message];
          });
          // A new user turn means the next assistant reply starts
          // fresh — reset the typewriter cursor so it begins at 0.
          currentAssistantIdRef.current = null;
          setTypedLen(0);
          return;
        }
        case "topic_shift": {
          setShift(evt.shift);
          return;
        }
        case "delta": {
          // Append into a streaming assistant placeholder at the
          // tail; create one if we don't have one yet.
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
            currentAssistantIdRef.current = fresh.id;
            return [...prev, fresh];
          });
          return;
        }
        case "tool_call": {
          const args =
            evt.args ??
            evt.arguments ??
            ({} as Record<string, unknown>);
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
              t.id === evt.id
                ? {
                    ...t,
                    result: normalized,
                  }
                : t,
            ),
          );
          return;
        }
        case "assistant_message": {
          // Merge the canonical body into the existing streaming
          // placeholder instead of replacing it with a brand-new
          // message. This keeps the client id stable, so the
          // typewriter's progress counter stays meaningful across
          // the finalization boundary. We *don't* flip streaming
          // off here — the cursor disappears on its own once the
          // typewriter catches up to body.length.
          setMessages((prev) => {
            const idx = findLastIndex(
              prev,
              (m) => m.role === "assistant" && !!m.streaming,
            );
            if (idx < 0) {
              return [
                ...prev,
                { ...evt.message, streaming: true },
              ];
            }
            const next = prev.slice();
            next[idx] = {
              ...next[idx],
              body: evt.message.body,
              meta: evt.message.meta ?? next[idx].meta,
            };
            return next;
          });
          return;
        }
        case "end": {
          setStreaming(false);
          setTools([]);
          return;
        }
        case "error": {
          setErrorText(evt.detail ?? evt.error ?? "Agent error");
          setStreaming(false);
          setTools([]);
          return;
        }
      }
    },
    [],
  );

  const send = useCallback(
    async (message: string, opts: { forceNewThread?: boolean } = {}) => {
      const trimmed = message.trim();
      if (!trimmed || streaming) return;
      setErrorText(null);
      setStreaming(true);
      setTools([]);

      // Optimistic user message so the textarea feels responsive.
      const optimistic: Message = {
        id: clientId(),
        role: "user",
        body: trimmed,
        streaming: false,
      };
      setMessages((prev) => [...prev, optimistic]);
      setDraft("");
      currentAssistantIdRef.current = null;
      setTypedLen(0);

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
      currentAssistantIdRef.current = null;
      setTypedLen(0);
    },
    [streaming, workspaceId],
  );

  const visibleMessages = useMemo(
    () => messages.filter((m) => m.role !== "system"),
    [messages],
  );
  const lastAssistantIndex = useMemo(() => {
    for (let i = visibleMessages.length - 1; i >= 0; i--) {
      if (visibleMessages[i].role === "assistant") return i;
    }
    return -1;
  }, [visibleMessages]);

  return (
    <div className="flex h-[calc(100vh-14rem)] min-h-[32rem] flex-col">
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
            {shift.reason ?? "Looks like you moved on."} Pack this thread
            into{" "}
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

      <div
        ref={scrollerRef}
        className="flex-1 overflow-y-auto py-4"
      >
        {visibleMessages.length === 0 ? (
          <EmptyHint />
        ) : (
          <div className="space-y-6">
            {visibleMessages.map((m, i) => (
              <MessageRow
                key={m.id}
                message={m}
                animate={i === lastAssistantIndex}
                typedLen={typedLen}
              />
            ))}
            {tools.length > 0 ? <ToolCallTrail rows={tools} /> : null}
          </div>
        )}
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
        <p className="font-semibold text-white/60">Single window, one chat.</p>
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
  typedLen,
}: {
  message: Message;
  animate: boolean;
  typedLen: number;
}) {
  const isUser = message.role === "user";
  const label = isUser ? "You" : message.role === "assistant" ? "Ship" : message.role;
  const labelTint = isUser ? "text-aqua/80" : "text-lilac/80";

  // For the last assistant message we render a prefix of the body
  // based on the typewriter cursor. Everything else renders fully.
  const display =
    animate && message.role === "assistant"
      ? message.body.slice(0, Math.max(0, Math.min(typedLen, message.body.length)))
      : message.body;
  const showCursor =
    animate && message.role === "assistant" && typedLen < message.body.length;
  const useMarkdown =
    !showCursor && (message.role === "assistant" || message.role === "user");

  return (
    <div className="text-[14px] leading-relaxed">
      <div
        className={`mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] ${labelTint}`}
      >
        {label}
      </div>
      {useMarkdown ? (
        <ChatMarkdown text={display} />
      ) : (
        <div className="whitespace-pre-wrap text-white/90">
          {display}
          {showCursor ? (
            <span className="ml-0.5 inline-block h-[0.9em] w-[2px] animate-pulse bg-white/60 align-[-0.1em]" />
          ) : null}
        </div>
      )}
    </div>
  );
}

function ToolCallTrail({ rows }: { rows: ToolCallRow[] }) {
  return (
    <div className="space-y-0.5 text-[11px] text-white/40">
      {rows.map((t) => (
        <div key={t.id} className="flex items-start gap-2">
          <span
            className={`mt-[7px] inline-block h-1 w-1 shrink-0 rounded-full ${
              t.result
                ? t.result.ok
                  ? "bg-emerald-400/80"
                  : "bg-rose-400/80"
                : "animate-pulse bg-aqua/80"
            }`}
          />
          <code className="flex-1 break-all font-mono">
            {t.name}({shortJson(t.args)})
            {t.result ? (
              t.result.ok ? (
                <span className="ml-1 text-emerald-400/70">→ ok</span>
              ) : (
                <span className="ml-1 text-rose-300/80">
                  → {t.result.error ?? "failed"}
                </span>
              )
            ) : null}
          </code>
        </div>
      ))}
    </div>
  );
}

function shortJson(value: unknown): string {
  try {
    const s = JSON.stringify(value);
    return s.length > 120 ? s.slice(0, 117) + "…" : s;
  } catch {
    return String(value);
  }
}

function normalizeToolResult(evt: {
  ok?: boolean;
  result?: unknown;
  error?: string;
  output?: string;
}): { ok: boolean; result?: unknown; error?: string } {
  if (typeof evt.ok === "boolean" && (evt.error !== undefined || evt.result !== undefined)) {
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

// ---------------------------------------------------------------------------
// SSE parser — minimal, compatible with the backend's
// ``event: <name>\ndata: <json>\n\n`` framing.
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
