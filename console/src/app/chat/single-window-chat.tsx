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
 * This file owns the SSE plumbing. The event protocol matches the
 * backend exactly (``thread`` / ``user_message`` / ``topic_shift``
 * / ``delta`` / ``tool_call`` / ``tool_result`` / ``assistant_message``
 * / ``end`` / ``error``); each type has a dedicated reducer that
 * mutates the local transcript state.
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
  | { type: "tool_call"; id: string; name: string; args: Record<string, unknown> }
  | {
      type: "tool_result";
      id: string;
      ok: boolean;
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

/** ``text-embedding`` etc. Produce one id per render burst. */
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

  const abortRef = useRef<AbortController | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  // Autoscroll to the newest message on every update. ``behavior:
  // "instant"`` because the stream lands in tight bursts and smooth
  // scroll animations end up chasing their own tail.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, tools, streaming]);

  useEffect(() => {
    // Always clean up the fetch abort controller if the component
    // unmounts mid-stream — the backend treats the dropped body as
    // "user went away" and stops generating.
    return () => {
      abortRef.current?.abort();
    };
  }, []);

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
            return [
              ...prev,
              {
                id: clientId(),
                role: "assistant",
                body: evt.text,
                streaming: true,
              },
            ];
          });
          return;
        }
        case "tool_call": {
          setTools((prev) => [
            ...prev,
            { id: evt.id, name: evt.name, args: evt.args },
          ]);
          return;
        }
        case "tool_result": {
          setTools((prev) =>
            prev.map((t) =>
              t.id === evt.id
                ? {
                    ...t,
                    result: { ok: evt.ok, result: evt.result, error: evt.error },
                  }
                : t,
            ),
          );
          return;
        }
        case "assistant_message": {
          // Replace the streaming placeholder with the canonical
          // message so the ids stay stable across refreshes.
          setMessages((prev) => {
            const trimmed = prev.filter((m) => !m.streaming);
            return [...trimmed, evt.message];
          });
          return;
        }
        case "end": {
          setStreaming(false);
          return;
        }
        case "error": {
          setErrorText(evt.detail ?? evt.error ?? "Agent error");
          setStreaming(false);
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

      // Optimistic user message so the textarea feels responsive.
      const optimistic: Message = {
        id: clientId(),
        role: "user",
        body: trimmed,
        streaming: false,
      };
      setMessages((prev) => [...prev, optimistic]);
      setDraft("");

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
    },
    [streaming, workspaceId],
  );

  const visibleMessages = useMemo(
    () => messages.filter((m) => m.role !== "system"),
    [messages],
  );

  return (
    <div className="flex h-[calc(100vh-16rem)] min-h-[32rem] flex-col">
      <div className="flex items-center gap-3 border-b border-white/10 pb-3">
        <h2 className="font-semibold text-white">{current.title}</h2>
        <span className="text-[11px] text-white/45">
          {current.status === "active" ? "live" : "archived"} ·{" "}
          {visibleMessages.length} msg
        </span>
        <button
          type="button"
          onClick={() => resetConversation({})}
          className="ml-auto rounded-md border border-white/15 bg-white/5 px-2 py-1 text-[11px] text-white/75 hover:border-white/30 hover:text-white"
          disabled={streaming}
        >
          New conversation
        </button>
      </div>

      {shift ? (
        <div className="mt-3 flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[12px] text-amber-100">
          <div className="flex-1">
            <strong className="font-semibold">Topic shift detected.</strong>{" "}
            {shift.reason ?? "Looks like you moved on."} Pack the current
            thread into{" "}
            <code className="rounded bg-black/30 px-1 py-0.5">
              {shift.suggested_bucket_name ?? "a new bucket"}
            </code>{" "}
            and start fresh?
          </div>
          <button
            type="button"
            onClick={() =>
              resetConversation({
                bucketName: shift.suggested_bucket_name ?? undefined,
              })
            }
            className="rounded-md border border-amber-400/60 bg-amber-400/10 px-2 py-1 text-[11px] font-semibold text-amber-100 hover:bg-amber-400/20"
          >
            Pack & start fresh
          </button>
          <button
            type="button"
            onClick={() => setShift(null)}
            className="rounded-md border border-white/10 px-2 py-1 text-[11px] text-white/60 hover:text-white"
          >
            Dismiss
          </button>
        </div>
      ) : null}

      <div
        ref={scrollerRef}
        className="mt-3 flex-1 overflow-y-auto rounded-xl border border-white/10 bg-black/30 p-4"
      >
        {visibleMessages.length === 0 ? (
          <EmptyHint />
        ) : (
          <ul className="space-y-4">
            {visibleMessages.map((m) => (
              <MessageBubble key={m.id} message={m} />
            ))}
            {tools.length > 0 ? <ToolCallStrip rows={tools} /> : null}
          </ul>
        )}
      </div>

      {errorText ? (
        <div className="mt-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-200">
          {errorText}
        </div>
      ) : null}

      <form onSubmit={onSubmit} className="mt-3 flex items-end gap-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask the agent. Enter to send, Shift-Enter for newline."
          rows={3}
          disabled={streaming}
          className="min-h-[64px] flex-1 resize-y rounded-md border border-white/10 bg-black/40 px-3 py-2 text-sm text-white placeholder-white/30 focus:border-aqua focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={streaming || draft.trim().length === 0}
          className="rounded-md bg-aqua px-4 py-2 text-sm font-semibold text-black hover:bg-aqua/90 disabled:cursor-not-allowed disabled:bg-aqua/40"
        >
          {streaming ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}

function EmptyHint() {
  return (
    <div className="flex h-full items-center justify-center text-center text-[12px] text-white/45">
      <div>
        <p className="font-semibold text-white/70">Single window, one chat.</p>
        <p className="mt-1">
          Ask the agent anything about this workspace. It can search the
          repo knowledge base, read files, create tickets, and file
          feedback against artifacts.
        </p>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const align = isUser ? "justify-end" : "justify-start";
  const bubble = isUser
    ? "bg-aqua/15 border-aqua/30 text-white"
    : "bg-white/[0.04] border-white/10 text-white/90";
  return (
    <li className={`flex ${align}`}>
      <div
        className={`max-w-[85%] whitespace-pre-wrap rounded-xl border px-3 py-2 text-sm ${bubble}`}
      >
        {message.body}
        {message.streaming ? (
          <span className="ml-1 inline-block h-3 w-2 animate-pulse rounded-sm bg-white/50 align-middle" />
        ) : null}
      </div>
    </li>
  );
}

function ToolCallStrip({ rows }: { rows: ToolCallRow[] }) {
  return (
    <li className="rounded-lg border border-white/5 bg-white/[0.02] p-2 text-[11px] text-white/60">
      <div className="mb-1 font-semibold uppercase tracking-wider text-white/40">
        Agent tools
      </div>
      <ul className="space-y-1">
        {rows.map((t) => (
          <li key={t.id} className="flex items-start gap-2">
            <span
              className={`mt-0.5 inline-block h-2 w-2 rounded-full ${
                t.result
                  ? t.result.ok
                    ? "bg-emerald-400"
                    : "bg-rose-400"
                  : "animate-pulse bg-aqua"
              }`}
            />
            <code className="flex-1 break-all text-white/80">
              {t.name}({shortJson(t.args)})
              {t.result && !t.result.ok ? (
                <span className="ml-1 text-rose-300">
                  {" "}
                  → {t.result.error ?? "failed"}
                </span>
              ) : null}
            </code>
          </li>
        ))}
      </ul>
    </li>
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
