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
 * - **Stateful animation.** Only segments born in this session
 *   animate. Historical segments hydrated from the server render
 *   statically, so reloading the page doesn't replay the last
 *   reply's reveal.
 * - **Thinking / tool cards.** Between the user's submit and the
 *   first delta, an animated "Thinking…" card holds the space so
 *   the page doesn't feel frozen. Tool calls render as styled
 *   pill-cards with a running shimmer while they're in flight.
 *
 * Wave B model:
 *   The transcript is a single ordered ``segments: Segment[]``
 *   array. Each ``user_message`` / ``delta`` / ``tool_call`` event
 *   appends a fresh segment in arrival order — when a ``tool_call``
 *   interrupts a streaming text segment, the *next* delta opens a
 *   new ``assistant_text`` segment so the rendered timeline reads
 *   prose₁ → tool₁ → prose₂ instead of all-prose-then-all-tools.
 *   This eliminates the "сверху-снизу-сверху" jumble that the
 *   previous dual-array (``messages[]`` + ``tools[]``) layout
 *   produced.
 *
 * The backend event protocol is unchanged (``thread`` /
 * ``user_message`` / ``topic_shift`` / ``delta`` / ``tool_call`` /
 * ``tool_result`` / ``assistant_message`` / ``end`` / ``error``).
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { ChatMarkdown, ChoiceProvider } from "./chat-markdown";
import {
  JsonFallback,
  TOOL_RENDERERS,
  friendlyToolVerb,
  renderToolResult,
} from "./tool-renderers";

type Role = "user" | "assistant" | "system" | "tool";

/**
 * SSE event payload shape — kept as a wire-format alias even though
 * component state no longer stores ``Message`` rows directly. The
 * backend ``user_message`` / ``assistant_message`` frames carry this
 * shape, and :func:`hydrateSegments` lifts initial-state messages
 * into the segment timeline.
 */
type Message = {
  id: string;
  role: Role;
  body: string;
  meta?: Record<string, unknown>;
  createdAt?: string;
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

/**
 * Single ordered timeline element. All three kinds are flat
 * peers — there is no "turn" wrapper. Render order = arrival
 * order; segment ids are stable for the lifetime of the segment
 * (we never reuse an id across kinds).
 *
 * - ``user``: a user prompt. Optimistic ids start with ``c_``;
 *   the persisted id (UUID from the backend) replaces the
 *   optimistic one when the ``user_message`` SSE frame lands.
 * - ``assistant_text``: a single contiguous run of model prose.
 *   A turn may produce **multiple** ``assistant_text`` segments
 *   when tool calls interleave between deltas — each interruption
 *   closes the current segment (``streaming: false``) so the next
 *   delta opens a fresh one. ``animate`` is true only when the
 *   segment is born live this session; hydrated history renders
 *   statically. ``serverMessageId`` is stamped by
 *   ``assistant_message`` (backend's persisted row id) for the
 *   *last* text segment of the turn.
 * - ``tool``: one tool call + (eventual) result. ``id`` matches
 *   the backend tool-call id so the ``tool_result`` frame can
 *   look it up. The card renders nothing visible until ``result``
 *   is populated — the in-flight status line does the talking.
 */
type UserSegment = {
  kind: "user";
  id: string;
  body: string;
  createdAt?: string;
  meta?: Record<string, unknown>;
};
type AssistantTextSegment = {
  kind: "assistant_text";
  id: string;
  body: string;
  streaming: boolean;
  animate: boolean;
  serverMessageId?: string;
  meta?: Record<string, unknown>;
};
type ToolSegment = {
  kind: "tool";
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: { ok: boolean; result?: unknown; error?: string };
};
type Segment = UserSegment | AssistantTextSegment | ToolSegment;

/**
 * Frontend-side topic-shift snapshot.
 *
 * Backend emits ``{ type: "topic_shift", decision: { shifted, reason,
 * new_title } }`` (see ``backend/app/api/v1/routes/chat.py``). We
 * keep the frontend shape flat for ergonomics — :func:`handleEvent`
 * unwraps ``decision`` into this struct. ``new_title`` is treated
 * as the suggested bucket name for the "pack & start fresh" CTA.
 *
 * No ``confidence`` field — backend doesn't ship one and the UI
 * never used it as a discriminator anyway.
 */
type TopicShift = {
  reason: string | null;
  new_title: string | null;
};

/**
 * Raw decision payload as it arrives over SSE. ``shifted`` is
 * filtered server-side (we only get a ``topic_shift`` event when
 * it's true) but we re-check defensively in case of contract drift.
 */
type TopicShiftDecision = {
  shifted?: boolean;
  reason?: string | null;
  new_title?: string | null;
};

type StreamEvent =
  | { type: "thread"; thread: Thread }
  | { type: "user_message"; message: Message }
  | { type: "topic_shift"; decision?: TopicShiftDecision; shift?: unknown }
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

function segId(): string {
  return "seg_" + Math.random().toString(36).slice(2, 10);
}

/**
 * Lift an array of persisted messages (initial state or post-reset
 * fresh thread) into the segment timeline. Tool messages from
 * history are intentionally skipped — historical tool-call
 * rehydration is Wave D. System messages stay hidden too.
 */
function hydrateSegments(messages: Message[]): Segment[] {
  const out: Segment[] = [];
  for (const m of messages) {
    if (m.role === "user") {
      out.push({
        kind: "user",
        id: m.id,
        body: m.body,
        createdAt: m.createdAt,
        meta: m.meta,
      });
    } else if (m.role === "assistant") {
      out.push({
        kind: "assistant_text",
        id: m.id,
        body: m.body,
        streaming: false,
        animate: false,
        serverMessageId: m.id,
        meta: m.meta,
      });
    }
  }
  return out;
}

// Reveal cadence for the word-by-word fade. We commit at most one
// word per tick, so the text feels "typed" rather than "pasted".
// Cadence is adaptive — if the buffer grows faster than we reveal,
// we speed up so the render doesn't trail the stream.
const REVEAL_MIN_DELAY = 18;
const REVEAL_FAR_DELAY = 35;

export function SingleWindowChat({ workspaceId, thread }: InitialState) {
  const [current, setCurrent] = useState<Thread>(thread);
  const [segments, setSegments] = useState<Segment[]>(() =>
    hydrateSegments(thread.messages),
  );
  const [shift, setShift] = useState<TopicShift | null>(null);
  const [streaming, setStreaming] = useState(false);
  // Distinguish "user submitted, nothing back yet" from "agent
  // started streaming text". Drives the Thinking card.
  const [awaitingFirstDelta, setAwaitingFirstDelta] = useState(false);
  const [draft, setDraft] = useState("");
  const [errorText, setErrorText] = useState<string | null>(null);

  // Per-assistant-text-segment reveal progress (prefix length).
  // Only the currently-streaming segment advances over time;
  // finalized segments sit at ``body.length`` and never re-animate.
  // Held in React state so the reveal tick triggers re-renders.
  const [revealed, setRevealed] = useState<Record<string, number>>({});
  const streamingIdRef = useRef<string | null>(null);

  // Reserved empty space below the last segment while a reply is
  // in flight. Without it the scroller has no room to scroll the
  // fresh user message to the top of the viewport — ``scrollTo``
  // silently no-ops because ``scrollHeight === clientHeight``. We
  // size it to the scroller's visible height on submit and release
  // it back to zero when the stream ends. This mirrors how ChatGPT
  // keeps the user prompt pinned at the top while the reply grows
  // in beneath it.
  const [bottomSpacerPx, setBottomSpacerPx] = useState(0);
  // Snapshot of the scroller's ``scrollTop`` and ``scrollHeight``
  // captured *before* the spacer changes. The companion
  // ``useLayoutEffect`` below uses it to restore the visible
  // anchor so collapsing the spacer at end-of-stream doesn't yank
  // the page upward under the reader's eyes. Reset to ``null``
  // when no compensation is pending.
  const spacerCompensationRef = useRef<{
    scrollTop: number;
    scrollHeight: number;
  } | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const lastUserAnchorRef = useRef<HTMLDivElement | null>(null);

  // Word-by-word reveal tick. Only the currently-streaming text
  // segment advances — everything else sits at full length. We
  // step one word per tick so bursts of delta chars don't dump
  // all at once.
  useEffect(() => {
    const id = streamingIdRef.current;
    if (!id) return;
    const seg = segments.find(
      (s): s is AssistantTextSegment =>
        s.kind === "assistant_text" && s.id === id,
    );
    if (!seg) return;
    const cur = revealed[id] ?? 0;
    if (cur >= seg.body.length) return;
    const idx = findNextWordBoundary(seg.body, cur);
    const nextLen = idx < 0 ? seg.body.length : idx;
    const remaining = seg.body.length - cur;
    // When the backend has already emitted the full body (the
    // segment was closed by a ``tool_call`` interruption or
    // ``end``) we fast-forward so the UI doesn't trail behind for
    // several seconds after the model stopped talking.
    const streamDone = !seg.streaming;
    const delay = streamDone
      ? 6
      : remaining > 300
        ? REVEAL_MIN_DELAY
        : REVEAL_FAR_DELAY;
    const h = window.setTimeout(() => {
      setRevealed((prev) => ({ ...prev, [id]: nextLen }));
    }, delay);
    return () => window.clearTimeout(h);
  }, [segments, revealed]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Spacer-shrink scroll compensation. Runs synchronously after
  // React commits a ``bottomSpacerPx`` change but before the
  // browser paints, so we can re-anchor the scroller to its
  // pre-shrink ``scrollTop`` and avoid the visible jump that
  // would otherwise happen when the runway disappears beneath
  // the pinned user message. ``spacerCompensationRef`` is
  // populated only by code paths that *want* the snap-back
  // (``end`` / ``error``) — growing the spacer on submit leaves
  // the ref empty and this is a no-op.
  useLayoutEffect(() => {
    const comp = spacerCompensationRef.current;
    if (!comp) return;
    spacerCompensationRef.current = null;
    const el = scrollerRef.current;
    if (!el) return;
    // If the new content is still tall enough to honour the old
    // ``scrollTop``, this restores the exact pre-shrink anchor.
    // If not, the browser will re-clamp to the new max — same
    // floor it would have hit anyway, just without our compensation
    // hiding the gap.
    el.scrollTop = comp.scrollTop;
  }, [bottomSpacerPx]);

  const handleEvent = useCallback((evt: StreamEvent) => {
    switch (evt.type) {
      case "thread": {
        setCurrent(evt.thread);
        return;
      }
      case "user_message": {
        setSegments((prev) => {
          // Drop any optimistic user segment (id starts ``c_``)
          // and append the persisted one in its place.
          const trimmed = prev.filter(
            (s) => !(s.kind === "user" && s.id.startsWith("c_")),
          );
          const incoming: UserSegment = {
            kind: "user",
            id: evt.message.id,
            body: evt.message.body,
            createdAt: evt.message.createdAt,
            meta: evt.message.meta,
          };
          return [...trimmed, incoming];
        });
        // Fresh turn — any in-flight assistant reveal is done; the
        // streaming slot is free until the first delta lands.
        streamingIdRef.current = null;
        return;
      }
      case "topic_shift": {
        // Backend ships ``{ decision: { shifted, reason, new_title } }``;
        // we flatten into our ``TopicShift`` shape. If a future
        // backend version starts emitting a different envelope we
        // warn once so contract drift surfaces in the console
        // before the banner silently disappears in production.
        const decision = evt.decision;
        if (
          decision &&
          typeof decision === "object" &&
          ("reason" in decision || "new_title" in decision)
        ) {
          setShift({
            reason:
              typeof decision.reason === "string" ? decision.reason : null,
            new_title:
              typeof decision.new_title === "string"
                ? decision.new_title
                : null,
          });
        } else {
          warnOnce(
            "topic_shift event missing `decision` payload",
            evt as unknown,
          );
        }
        return;
      }
      case "delta": {
        setAwaitingFirstDelta(false);
        setSegments((prev) => {
          const last = prev[prev.length - 1];
          if (
            last &&
            last.kind === "assistant_text" &&
            last.streaming
          ) {
            const next = prev.slice(0, -1);
            next.push({ ...last, body: last.body + evt.text });
            return next;
          }
          // No open text segment — either this is the turn's first
          // prose, or a ``tool_call`` interruption closed the
          // previous one. Either way, open a new segment so the
          // tool card stays visually between the two prose blocks.
          const fresh: AssistantTextSegment = {
            kind: "assistant_text",
            id: segId(),
            body: evt.text,
            streaming: true,
            animate: true,
          };
          streamingIdRef.current = fresh.id;
          // Seed the reveal at 0 so the first word fades in
          // rather than popping in wholesale.
          setRevealed((r) => ({ ...r, [fresh.id]: 0 }));
          return [...prev, fresh];
        });
        return;
      }
      case "tool_call": {
        const args =
          evt.args ?? evt.arguments ?? ({} as Record<string, unknown>);
        setSegments((prev) => {
          const next = prev.slice();
          // Side effect: close out any open assistant_text segment
          // so the *next* delta opens a fresh one *after* the tool
          // card. This is what makes prose₁ → tool → prose₂ render
          // in source order instead of being clumped.
          const lastIdx = next.length - 1;
          const last = next[lastIdx];
          if (
            last &&
            last.kind === "assistant_text" &&
            last.streaming
          ) {
            next[lastIdx] = { ...last, streaming: false };
            // The previous segment is now finalized; the next
            // delta should open a brand-new segment, so clear the
            // ref so the ``delta`` branch's "find open" check
            // misses and forces an append.
            if (streamingIdRef.current === last.id) {
              streamingIdRef.current = null;
            }
          }
          next.push({
            kind: "tool",
            id: evt.id,
            name: evt.name,
            args,
          });
          return next;
        });
        return;
      }
      case "tool_result": {
        const normalized = normalizeToolResult(evt);
        setSegments((prev) =>
          prev.map((s) =>
            s.kind === "tool" && s.id === evt.id
              ? { ...s, result: normalized }
              : s,
          ),
        );
        return;
      }
      case "assistant_message": {
        setSegments((prev) => {
          // Stamp ``serverMessageId`` on the *last* text segment
          // that hasn't been finalized yet. Don't rewrite ``body``
          // — the concatenation of all this turn's
          // ``assistant_text`` segments IS the persisted message,
          // and rewriting would clobber the segment splits we
          // need to keep the prose/tool/prose layout intact.
          const idx = findLastIndex(
            prev,
            (s) => s.kind === "assistant_text" && !s.serverMessageId,
          );
          if (idx < 0) {
            // Pure-tool turn (no prose at all) — append a synthetic
            // finalized text segment from the persisted body so
            // the row still appears in the timeline.
            return [
              ...prev,
              {
                kind: "assistant_text",
                id: evt.message.id,
                body: evt.message.body,
                streaming: false,
                animate: true,
                serverMessageId: evt.message.id,
                meta: evt.message.meta,
              },
            ];
          }
          const next = prev.slice();
          const seg = next[idx] as AssistantTextSegment;
          next[idx] = {
            ...seg,
            serverMessageId: evt.message.id,
            meta: { ...(seg.meta ?? {}), ...(evt.message.meta ?? {}) },
          };
          return next;
        });
        return;
      }
      case "end": {
        setStreaming(false);
        setAwaitingFirstDelta(false);
        // Release the runway spacer now that the turn is over so
        // we don't leave an empty tail hanging under the reply
        // until the next ``send``. We capture the scroller's
        // current ``scrollTop`` here, *before* the spacer state
        // change is committed, so the companion ``useLayoutEffect``
        // can snap the scroll position back after React mutates
        // the DOM. Without that compensation the browser would
        // clamp ``scrollTop`` against the now-shorter content and
        // the pinned user prompt would visibly jump down inside
        // the viewport.
        const scroller = scrollerRef.current;
        if (scroller) {
          spacerCompensationRef.current = {
            scrollTop: scroller.scrollTop,
            scrollHeight: scroller.scrollHeight,
          };
        }
        setBottomSpacerPx(0);
        setSegments((prev) => {
          const idx = findLastIndex(
            prev,
            (s) => s.kind === "assistant_text" && s.streaming,
          );
          if (idx < 0) return prev;
          const next = prev.slice();
          const seg = next[idx] as AssistantTextSegment;
          next[idx] = { ...seg, streaming: false };
          return next;
        });
        return;
      }
      case "error": {
        setErrorText(evt.detail ?? evt.error ?? "Agent error");
        setStreaming(false);
        setAwaitingFirstDelta(false);
        // Symmetric with ``end``: free the spacer with the same
        // scrollTop-preservation dance. Leaving it pinned here
        // would strand an empty runway below the error message
        // until the user sends another turn.
        const scroller = scrollerRef.current;
        if (scroller) {
          spacerCompensationRef.current = {
            scrollTop: scroller.scrollTop,
            scrollHeight: scroller.scrollHeight,
          };
        }
        setBottomSpacerPx(0);
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

      const optimisticId = clientId();
      const optimistic: UserSegment = {
        kind: "user",
        id: optimisticId,
        body: trimmed,
      };
      setSegments((prev) => [...prev, optimistic]);
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
      setSegments(hydrateSegments(fresh.messages ?? []));
      setShift(null);
      streamingIdRef.current = null;
      setRevealed({});
      setBottomSpacerPx(0);
    },
    [streaming, workspaceId],
  );

  const lastUserIndex = useMemo(() => {
    for (let i = segments.length - 1; i >= 0; i--) {
      if (segments[i].kind === "user") return i;
    }
    return -1;
  }, [segments]);
  const userSegmentCount = useMemo(
    () => segments.reduce((n, s) => (s.kind === "user" ? n + 1 : n), 0),
    [segments],
  );
  const hasStreamingAssistant = useMemo(
    () =>
      segments.some(
        (s) => s.kind === "assistant_text" && s.streaming,
      ),
    [segments],
  );
  // Latest tool segment within the current turn (= after the last
  // user prompt). Pre-Wave-B this was driven by a separate
  // ``tools[]`` array that we cleared on each ``send``; now we
  // derive it positionally so historical tools from earlier turns
  // never leak into the live status line.
  const currentTurnLastTool = useMemo<ToolSegment | null>(() => {
    for (let i = segments.length - 1; i > lastUserIndex; i--) {
      const s = segments[i];
      if (s.kind === "tool") return s;
    }
    return null;
  }, [segments, lastUserIndex]);
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
    const latest = currentTurnLastTool;
    if (latest && !latest.result) {
      return {
        tone: "shimmer",
        text: friendlyToolVerb(latest.name),
      };
    }
    if (latest && latest.result && !latest.result.ok) {
      // Keep the error wording in the same gentle register as the
      // friendly verbs above — short reason inline, the tool card
      // below carries the real diagnostic in a <details>.
      const reason = latest.result.error ?? "something went wrong";
      return {
        tone: "error",
        text: `Hit a snag — ${reason}`,
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
  }, [streaming, hasStreamingAssistant, currentTurnLastTool, awaitingFirstDelta]);

  // Map each assistant_text segment to how many characters we
  // should render right now. Non-streaming segments (historical +
  // finalized past turns) always show their full body; only the
  // currently active assistant slot is clamped by the reveal tick.
  const revealMap = useMemo(() => {
    const map = new Map<string, number>();
    for (const s of segments) {
      if (s.kind !== "assistant_text") continue;
      if (s.id === streamingIdRef.current) {
        const cur = revealed[s.id] ?? 0;
        map.set(s.id, Math.min(cur, s.body.length));
      } else {
        map.set(s.id, s.body.length);
      }
    }
    return map;
  }, [segments, revealed]);

  return (
    <div className="flex h-[calc(100vh-12rem)] min-h-[34rem] flex-col">
      <div className="flex items-center gap-3 pb-2">
        <h2 className="text-sm font-semibold text-white/90">{current.title}</h2>
        <span className="text-[11px] text-white/35">
          {current.status === "active" ? "live" : "archived"} ·{" "}
          {userSegmentCount} msg
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
        <TopicShiftBanner
          shift={shift}
          onPack={() =>
            resetConversation({
              bucketName: shift.new_title ?? undefined,
            })
          }
          onDismiss={() => setShift(null)}
        />
      ) : null}

      <div ref={scrollerRef} className="flex-1 overflow-y-auto py-4">
        <ChoiceProvider onChoose={(label) => void send(label)}>
          {segments.length === 0 ? (
            <EmptyHint />
          ) : (
            <div className="space-y-6">
              {/* Single ordered timeline. user / assistant_text /
                  tool segments render in arrival order — when a
                  ``tool_call`` interleaves between deltas, the
                  next prose segment naturally lands *below* the
                  tool card, eliminating the dual-array clumping
                  that pre-Wave-B pinned all tools at the bottom. */}
              {segments.map((seg, i) => {
                if (seg.kind === "user") {
                  return (
                    <UserRow
                      key={seg.id}
                      segment={seg}
                      anchorRef={
                        i === lastUserIndex ? lastUserAnchorRef : null
                      }
                    />
                  );
                }
                if (seg.kind === "assistant_text") {
                  return (
                    <AssistantTextRow
                      key={seg.id}
                      segment={seg}
                      revealLen={revealMap.get(seg.id) ?? seg.body.length}
                    />
                  );
                }
                return <ToolSegmentRow key={seg.id} segment={seg} />;
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

function UserRow({
  segment,
  anchorRef,
}: {
  segment: UserSegment;
  anchorRef: React.RefObject<HTMLDivElement | null> | null;
}) {
  return (
    <div ref={anchorRef ?? undefined} className="text-[14px] leading-relaxed">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-aqua/80">
        You
      </div>
      <ChatMarkdown text={segment.body} animate={false} />
    </div>
  );
}

function AssistantTextRow({
  segment,
  revealLen,
}: {
  segment: AssistantTextSegment;
  revealLen: number;
}) {
  const displayBody = segment.body.slice(
    0,
    Math.max(0, Math.min(revealLen, segment.body.length)),
  );
  return (
    <div className="text-[14px] leading-relaxed">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-lilac/80">
        Ship
      </div>
      <ChatMarkdown text={displayBody} animate={segment.animate} />
    </div>
  );
}

/**
 * Per-segment tool renderer.
 *
 * In-flight tools (no ``result`` yet) render nothing — the
 * single-line ``TurnStatusLine`` below the timeline is the
 * progress signal. Once the result lands we route through
 * :func:`renderToolResult` for tools that have a registered rich
 * renderer, falling back to :class:`JsonFallback` so unknown
 * tools stay readable. Errored results (``ok === false``) get the
 * uniform ErrorCard treatment via :func:`renderToolResult`.
 */
function ToolSegmentRow({ segment }: { segment: ToolSegment }) {
  const result = segment.result;
  if (!result) return null;
  if (!result.ok) {
    return renderToolResult(segment.name, {
      error: result.error ?? "failed",
    }) as React.ReactNode;
  }
  if (TOOL_RENDERERS[segment.name]) {
    return renderToolResult(segment.name, result.result) as React.ReactNode;
  }
  return <JsonFallback toolName={segment.name} result={result.result} />;
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

/**
 * Banner shown above the chat thread when the backend's topic
 * classifier flags that the user has moved on. The shape comes
 * straight from ``TopicShift`` (see top-of-file note) — backend
 * sends ``decision.new_title`` which we treat here as the
 * suggested bucket name for the "pack & start fresh" CTA.
 */
function TopicShiftBanner({
  shift,
  onPack,
  onDismiss,
}: {
  shift: TopicShift;
  onPack: () => void;
  onDismiss: () => void;
}) {
  const bucketName = shift.new_title;
  return (
    <div className="flex items-start gap-3 py-2 text-[12px] text-amber-200/90">
      <span className="mt-0.5 h-1 w-1 shrink-0 rounded-full bg-amber-300" />
      <div className="flex-1">
        <strong className="font-semibold">Topic shift.</strong>{" "}
        {shift.reason ?? "Looks like you moved on."} Pack this thread into{" "}
        <code className="text-amber-100">{bucketName ?? "a new bucket"}</code>?
        <button
          type="button"
          onClick={onPack}
          className="ml-2 font-semibold text-amber-100 underline-offset-2 hover:underline"
        >
          pack & start fresh
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="ml-2 text-white/40 hover:text-white/80"
        >
          dismiss
        </button>
      </div>
    </div>
  );
}

/**
 * One-shot ``console.warn`` keyed by message string so contract-
 * drift warnings (e.g. SSE event missing an expected field) don't
 * spam the devtools when the same bad payload arrives repeatedly.
 */
const _warnedKeys = new Set<string>();
function warnOnce(message: string, payload?: unknown): void {
  if (_warnedKeys.has(message)) return;
  _warnedKeys.add(message);
  console.warn(`[chat] ${message}`, payload);
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
