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

import Link from "next/link";
import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";

import { ChatMarkdown, ChoiceProvider } from "./chat-markdown";
import {
  isAllowedAttachment,
  resolveMime,
} from "@/lib/attachment-policy";
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
  // E20 — current drafting mode. ``null`` for ordinary chat,
  // ``"shape_project"`` while the thread is in drafting mode.
  // Flipped in place via POST /chat/active/intent (E20-1).
  intent: "shape_project" | null;
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
  /**
   * Stable React key for the row's lifetime. For optimistic
   * segments this is the ``c_*`` client id; we deliberately do
   * **not** swap it for the persisted UUID when the
   * ``user_message`` SSE frame lands — swapping would change the
   * React key and remount the row, producing a visible blink on
   * every send. The persisted UUID lands on ``serverId``.
   */
  id: string;
  /** Persisted backend UUID, populated once the SSE frame lands. */
  serverId?: string;
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
 * Render-time row produced by :func:`buildRenderRows`. The chat
 * timeline iterates these instead of raw :data:`Segment` because
 * tool segments are not rendered inline — they fold into the
 * *last* ``assistant_text`` of their turn, accessible through a
 * `Show details` disclosure attached to that assistant row.
 *
 * ``userIndex`` is the running 0-based count of user rows seen so
 * far; the consumer compares it to ``lastUserIndex`` to decide
 * which user message gets the scroll anchor.
 */
type AssistantRenderRow = {
  kind: "assistant_text";
  segment: AssistantTextSegment;
  tools: ToolSegment[];
};
type UserRenderRow = {
  kind: "user";
  segment: UserSegment;
  userIndex: number;
};
type RenderRow = UserRenderRow | AssistantRenderRow;

function buildRenderRows(segments: Segment[]): RenderRow[] {
  const rows: RenderRow[] = [];
  let pendingAssistants: AssistantTextSegment[] = [];
  let pendingTools: ToolSegment[] = [];
  let userIndex = 0;

  // Tools accumulate across every assistant_text in the same turn,
  // then attach to the LAST one when the turn ends (next user msg
  // arrives) or the timeline runs out. Earlier assistant_text rows
  // in the same turn render without a disclosure — they're just
  // intermediate prose chunks the model emitted between tool calls.
  const flushPending = () => {
    for (let i = 0; i < pendingAssistants.length; i++) {
      const isLast = i === pendingAssistants.length - 1;
      rows.push({
        kind: "assistant_text",
        segment: pendingAssistants[i],
        tools: isLast ? pendingTools : [],
      });
    }
    pendingAssistants = [];
    pendingTools = [];
  };

  for (const seg of segments) {
    if (seg.kind === "user") {
      flushPending();
      rows.push({ kind: "user", segment: seg, userIndex });
      userIndex += 1;
    } else if (seg.kind === "assistant_text") {
      pendingAssistants.push(seg);
    } else {
      pendingTools.push(seg);
    }
  }
  flushPending();
  return rows;
}

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
  /**
   * True when the user said something canonical like "let's switch"
   * / "теперь другое" — the backend's regex pre-filter caught it
   * directly so we elevate the banner copy from "looks like you
   * moved on" to a more decisive "switching now". The UX still
   * confirms before packing — auto-action with undo is a follow-up.
   */
  explicitPhrase: boolean;
};

/**
 * One bucket-article hit returned by the backend's per-turn
 * retrieval. We render these inside a small "Using N prior
 * memories" disclosure under the user message so the user can see
 * what the agent is leaning on without forcing the panel open.
 */
type RetrievedContext = {
  hits: Array<{
    bucket_slug: string;
    bucket_name: string;
    title: string;
    similarity: number;
  }>;
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
  explicit_phrase?: boolean;
};

type StreamEvent =
  | { type: "thread"; thread: Thread }
  | { type: "user_message"; message: Message }
  | { type: "topic_shift"; decision?: TopicShiftDecision; shift?: unknown }
  | {
      // E20-2 — drafting-intent classifier verdict. Renders an
      // inline CTA below the assistant's reply suggesting the user
      // switch into / out of drafting mode in place.
      type: "drafting_intent";
      verdict: "ENTER" | "EXIT";
      reason?: string;
      suggested_title?: string | null;
    }
  | {
      type: "retrieved_context";
      hits?: Array<{
        bucket_slug?: string;
        bucket_name?: string;
        title?: string;
        similarity?: number;
      }>;
    }
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
  /**
   * Server-side probe for "does this workspace have at least one
   * archived chat?". Drives the optional "View archived chats"
   * link in :func:`EmptyHint`. Computed in ``page.tsx`` because
   * the API client is server-only — we don't have a client-safe
   * fetcher for this and it's not worth standing up an API route
   * for one boolean. False on failure (silent).
   */
  hasArchivedChats?: boolean;
};

function clientId(): string {
  return "c_" + Math.random().toString(36).slice(2, 10);
}

function segId(): string {
  return "seg_" + Math.random().toString(36).slice(2, 10);
}

/**
 * Lift an array of persisted messages (initial state or post-reset
 * fresh thread) into the segment timeline.
 *
 * Wave D: assistant messages with persisted ``meta.tool_invocations``
 * also emit one ``tool`` segment per invocation, **after** the
 * corresponding ``assistant_text`` segment. The wire-order
 * interleaving from Wave B (prose₁ → tool → prose₂) is lost on
 * reload because we don't persist segment splits — the priority is
 * just to make the tool cards visible at all so reloading a thread
 * doesn't silently drop tool history. Persisting segment order is
 * a backend change and out of scope here.
 *
 * Standalone ``tool`` role messages from history are still skipped
 * — every tool call belongs to an assistant turn whose persisted
 * meta already lists it. System messages stay hidden too.
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
      // Rehydrate tool cards from persisted ``meta.tool_invocations``.
      // Backend (``_run_agent_turn``) writes one entry per tool call
      // with ``{id, name, arguments, output}`` where ``output`` is a
      // JSON-encoded string (the same string fed back to the model
      // as the tool message body). Reuse :func:`normalizeToolResult`
      // so error detection mirrors the live SSE path exactly.
      const meta = m.meta;
      if (meta && typeof meta === "object" && !Array.isArray(meta)) {
        const raw = (meta as Record<string, unknown>).tool_invocations;
        if (raw === undefined) {
          // Most assistant rows pre-Wave-D have no tool history —
          // absence is normal, not malformed.
        } else if (!Array.isArray(raw)) {
          warnOnce(
            "assistant message meta.tool_invocations malformed",
            meta,
          );
        } else {
          for (const inv of raw) {
            if (!inv || typeof inv !== "object") continue;
            const i = inv as Record<string, unknown>;
            const invId = typeof i.id === "string" ? i.id : segId();
            const name = typeof i.name === "string" ? i.name : "tool";
            const args =
              i.arguments && typeof i.arguments === "object"
                ? (i.arguments as Record<string, unknown>)
                : {};
            const output = typeof i.output === "string" ? i.output : undefined;
            const result = normalizeToolResult({ output });
            out.push({
              kind: "tool",
              // Prefix with the assistant message id so tool ids
              // can't collide across turns (defensive — backend mints
              // unique tc.ids per turn but cheap insurance).
              id: `${m.id}:${invId}`,
              name,
              args,
              result,
            });
          }
        }
      }
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

export function SingleWindowChat({
  workspaceId,
  thread,
  hasArchivedChats = false,
}: InitialState) {
  const [current, setCurrent] = useState<Thread>(thread);
  const [segments, setSegments] = useState<Segment[]>(() =>
    hydrateSegments(thread.messages),
  );
  const [shift, setShift] = useState<TopicShift | null>(null);
  // E20-3 — drafting-intent CTA. Backend emits a verdict once per
  // user turn; we hold it here until the user clicks Switch / Exit
  // or dismisses. Reset on a fresh user message so a stale CTA
  // can't linger across turns.
  const [draftingSuggestion, setDraftingSuggestion] = useState<{
    verdict: "ENTER" | "EXIT";
    reason: string;
    suggestedTitle: string | null;
  } | null>(null);
  const [draftingFlipPending, setDraftingFlipPending] = useState(false);
  // Per-user-message ``retrieved_context`` payload, keyed by user
  // message id. Each turn's retrieval lookup lands here once the
  // backend emits the SSE event; the matching user row renders the
  // "Using N prior memories" disclosure inline. Keyed-by-id (not
  // reset on each turn) because past disclosures stay visible as
  // historical breadcrumbs.
  const [retrievedByUserId, setRetrievedByUserId] = useState<
    Record<string, RetrievedContext>
  >({});
  // Tracks the most-recent **persisted** user message id so the
  // ``retrieved_context`` handler can attach hits to it. The
  // optimistic ``c_<rand>`` id from ``send`` is replaced by a
  // server-assigned UUID via the ``user_message`` event before
  // retrieval fires, so we always store the canonical id here.
  const lastUserIdRef = useRef<string | null>(null);
  // Attached files staged before the next ``send``. Per-message caps
  // and MIME whitelist mirror ``backend.app.services.attachments.policy``
  // so the user gets a friendly error in the composer rather than a
  // 415 from the server after upload.
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
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
  // Two compensation modes:
  //
  // - ``anchorOffset`` — px from the scroller's top edge to the
  //   ``lastUserAnchorRef`` element's top edge, captured *before*
  //   the spacer collapses. After collapse we re-pin the anchor to
  //   the same offset so the conversation stays exactly where the
  //   reader was, regardless of browser scrollTop clamping. This is
  //   the preferred path because raw ``scrollTop`` preservation
  //   doesn't survive a maxScroll shrink (browser clamps the value
  //   downward, visibly jumping the viewport).
  // - ``scrollTop`` — fallback when no anchor is mounted (e.g. the
  //   error path before the first user message lands).
  const spacerCompensationRef = useRef<
    | { mode: "anchor"; anchorOffset: number }
    | { mode: "scrollTop"; scrollTop: number }
    | null
  >(null);

  const abortRef = useRef<AbortController | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const lastUserAnchorRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const chatRootRef = useRef<HTMLDivElement | null>(null);
  // Last prompt body the user submitted, captured at the start of
  // every ``send``. The error-card "Retry" button replays it so a
  // failed stream isn't lost — the user doesn't have to retype.
  const lastUserPromptRef = useRef<string | null>(null);

  // Distance between the scroller's bottom and the visible viewport
  // edge. Drives the "Jump to latest" pill — we treat anything > 120
  // as "user has scrolled away on purpose" and surface the affordance.
  const [scrollGap, setScrollGap] = useState(0);

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

  // Track how far the scroller is from the bottom so the
  // "Jump to latest" pill can fade in once the user has scrolled
  // back through history. rAF-throttled so a fast trackpad
  // scroll doesn't drown React in re-renders.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      setScrollGap(el.scrollHeight - el.clientHeight - el.scrollTop);
    };
    const onScroll = () => {
      if (raf !== 0) return;
      raf = requestAnimationFrame(update);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    update();
    return () => {
      el.removeEventListener("scroll", onScroll);
      if (raf !== 0) cancelAnimationFrame(raf);
    };
  }, []);

  // Re-evaluate the scroll gap whenever content height could
  // change (new segments, runway grows/shrinks). Keeps the
  // "Jump to latest" pill in sync without needing a ResizeObserver.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    setScrollGap(el.scrollHeight - el.clientHeight - el.scrollTop);
  }, [segments, bottomSpacerPx]);

  // Opening Navigator (or navigating back to /chat) remounts this
  // client; the scroll container defaults to the top while hydrated
  // history renders from the first message. Snap to the bottom once
  // so the latest turn is in view — same idea as chat apps on reopen.
  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (!el || thread.messages.length === 0) return;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const s = scrollerRef.current;
        if (!s) return;
        s.scrollTop = s.scrollHeight - s.clientHeight;
        setScrollGap(s.scrollHeight - s.clientHeight - s.scrollTop);
      });
    });
    // Intentionally mount-only: in-session scroll discipline is handled
    // by submit / stream / ``Jump to latest`` — we must not fight that.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    if (comp.mode === "anchor") {
      // Re-pin the last user message to its pre-collapse offset
      // from the scroller's top edge. After ``setBottomSpacerPx(0)``
      // the content above the spacer is unchanged, but the total
      // scrollHeight has shrunk; setting raw ``scrollTop`` to the
      // pre-collapse value gets clamped by the browser whenever the
      // captured value exceeds the new maxScroll, and the viewport
      // visibly drops to the bottom of the now-shorter content.
      // Anchor-relative re-pinning side-steps that — we measure the
      // anchor's current top, work out the delta from where it
      // *should* be, and adjust ``scrollTop`` by exactly that delta.
      const anchor = lastUserAnchorRef.current;
      if (anchor) {
        const scrollerTop = el.getBoundingClientRect().top;
        const anchorTop = anchor.getBoundingClientRect().top;
        const currentOffset = anchorTop - scrollerTop;
        const delta = currentOffset - comp.anchorOffset;
        if (delta !== 0) {
          el.scrollTop = Math.max(0, el.scrollTop + delta);
        }
      }
      return;
    }
    // Fallback: raw scrollTop. Browser will clamp if the new
    // content is shorter — same floor it would have hit anyway.
    el.scrollTop = comp.scrollTop;
  }, [bottomSpacerPx]);

  // Snap the bottom runway closed while preserving the visible
  // anchor. Captures ``scrollTop`` synchronously so the layout-
  // effect compensation can re-pin after React mutates the DOM.
  // Called from ``end``/``error`` SSE handlers and the manual
  // stop-streaming button; all three exit paths need the same
  // dance, so factoring keeps them in sync.
  const releaseSpacer = useCallback(() => {
    const scroller = scrollerRef.current;
    if (scroller) {
      const anchor = lastUserAnchorRef.current;
      if (anchor) {
        const scrollerTop = scroller.getBoundingClientRect().top;
        const anchorTop = anchor.getBoundingClientRect().top;
        spacerCompensationRef.current = {
          mode: "anchor",
          anchorOffset: anchorTop - scrollerTop,
        };
      } else {
        spacerCompensationRef.current = {
          mode: "scrollTop",
          scrollTop: scroller.scrollTop,
        };
      }
    }
    setBottomSpacerPx(0);
  }, []);

  const handleEvent = useCallback((evt: StreamEvent) => {
    switch (evt.type) {
      case "thread": {
        setCurrent(evt.thread);
        return;
      }
      case "user_message": {
        // Fresh user turn — drop any stale drafting-intent CTA so
        // the next verdict can render cleanly.
        setDraftingSuggestion(null);
        setSegments((prev) => {
          // Stamp the persisted UUID onto the most recent optimistic
          // user segment without changing its ``id`` (= React key).
          // Swapping the React key would unmount the row → visible
          // blink on every send. We keep the ``c_*`` id forever and
          // park the backend uuid on ``serverId``.
          const idx = findLastIndex(
            prev,
            (s) => s.kind === "user" && !(s as UserSegment).serverId,
          );
          if (idx >= 0) {
            const next = prev.slice();
            const opt = next[idx] as UserSegment;
            next[idx] = {
              ...opt,
              serverId: evt.message.id,
              body: evt.message.body,
              createdAt: evt.message.createdAt,
              meta: evt.message.meta,
            };
            return next;
          }
          return [
            ...prev,
            {
              kind: "user",
              id: evt.message.id,
              serverId: evt.message.id,
              body: evt.message.body,
              createdAt: evt.message.createdAt,
              meta: evt.message.meta,
            },
          ];
        });
        // Fresh turn — any in-flight assistant reveal is done; the
        // streaming slot is free until the first delta lands.
        streamingIdRef.current = null;
        // Remember the canonical user id so the upcoming
        // ``retrieved_context`` event can be attached to this row.
        lastUserIdRef.current = evt.message.id;
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
            explicitPhrase: decision.explicit_phrase === true,
          });
        } else {
          warnOnce(
            "topic_shift event missing `decision` payload",
            evt as unknown,
          );
        }
        return;
      }
      case "drafting_intent": {
        // E20-2 — drafting-intent classifier verdict. We stash the
        // suggestion + render an inline CTA the user can click to
        // flip ``thread.intent`` in place.
        if (evt.verdict !== "ENTER" && evt.verdict !== "EXIT") return;
        setDraftingSuggestion({
          verdict: evt.verdict,
          reason: typeof evt.reason === "string" ? evt.reason : "",
          suggestedTitle:
            typeof evt.suggested_title === "string"
              ? evt.suggested_title
              : null,
        });
        return;
      }
      case "retrieved_context": {
        // Backend emits this once per turn AFTER it's pulled the
        // top-K bucket articles for the new user message. Stash on
        // the most-recent user segment (via the controlled key) so
        // the disclosure renders right under the message that
        // triggered the lookup. Empty hits aren't sent, but defend
        // against contract drift by skipping when the array's empty.
        const hits = Array.isArray(evt.hits) ? evt.hits : [];
        const cleaned = hits
          .map((h) => ({
            bucket_slug: typeof h.bucket_slug === "string" ? h.bucket_slug : "",
            bucket_name: typeof h.bucket_name === "string" ? h.bucket_name : "",
            title: typeof h.title === "string" ? h.title : "",
            similarity: typeof h.similarity === "number" ? h.similarity : 0,
          }))
          .filter((h) => h.title || h.bucket_slug);
        if (cleaned.length === 0) return;
        const userId = lastUserIdRef.current;
        if (!userId) return;
        setRetrievedByUserId((m) => ({
          ...m,
          [userId]: { hits: cleaned },
        }));
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
        // Stop the per-word reveal tick AND force the dying
        // segment to render its full body in the next commit.
        // ``revealMap`` keys off ``streamingIdRef.current`` —
        // clearing it here makes the upcoming render fall into
        // the "not streaming" branch which serves ``body.length``
        // verbatim. Without this, the reveal effect kept fast-
        // forwarding for ~100ms after end, mutating content height
        // *while* the spacer was collapsing, and the combined
        // shrink + grow nudged the viewport downward — the jolt
        // that made the chat feel like it was "scrolling down" at
        // end-of-stream.
        streamingIdRef.current = null;
        // Release the runway spacer now that the turn is over so
        // we don't leave an empty tail hanging under the reply
        // until the next ``send``. ``releaseSpacer`` captures an
        // anchor-relative offset for the last user message so the
        // layout effect can re-pin the conversation after React
        // shrinks the spacer — without that, browser ``scrollTop``
        // clamping pushes the viewport to the bottom of the now-
        // shorter content.
        releaseSpacer();
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
        // Mirror ``end``: stop the reveal tick + release the spacer
        // with the same anchor-relative dance. A streaming segment
        // mid-error would otherwise keep fast-forwarding while the
        // error card mounts, and the combined motion produced the
        // same end-of-stream jolt the user complained about.
        streamingIdRef.current = null;
        releaseSpacer();
        return;
      }
    }
  }, [releaseSpacer]);

  const acceptFiles = useCallback(
    (incoming: File[]) => {
      // Phase 3 policy mirror — keep in sync with
      // ``backend.app.services.attachments.policy``. The client-side
      // copy lets us reject in the composer without round-tripping a
      // 5 MiB image to discover it was the wrong MIME.
      const MAX_FILES = 5;
      const MAX_PER_FILE = 10 * 1024 * 1024;
      const MAX_TOTAL = 30 * 1024 * 1024;
      setAttachError(null);
      const normalizedIncoming = incoming.map((f) => {
        const mime = resolveMime(f.name, f.type);
        if (mime === f.type) return f;
        return new File([f], f.name, { type: mime });
      });
      const combined = [...pendingFiles, ...normalizedIncoming];
      if (combined.length > MAX_FILES) {
        setAttachError(`Up to ${MAX_FILES} files per message.`);
        return;
      }
      let total = 0;
      for (const f of combined) {
        if (!isAllowedAttachment(f.name, f.type)) {
          setAttachError(
            `${f.name}: type ${f.type || "unknown"} isn't supported. ` +
              "Allowed: jpeg/png/webp/gif, PDF, txt/md/csv/json/yaml.",
          );
          return;
        }
        if (f.size > MAX_PER_FILE) {
          setAttachError(`${f.name}: file too large (>10 MiB).`);
          return;
        }
        total += f.size;
      }
      if (total > MAX_TOTAL) {
        setAttachError("Combined attachments exceed 30 MiB.");
        return;
      }
      setPendingFiles(combined);
    },
    [pendingFiles],
  );

  const removePendingFile = useCallback((idx: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== idx));
    setAttachError(null);
  }, []);

  const send = useCallback(
    async (message: string, opts: { forceNewThread?: boolean } = {}) => {
      const trimmed = message.trim();
      if ((!trimmed && pendingFiles.length === 0) || streaming) return;
      // Stash the prompt before we kick off so an in-flight error
      // can be retried. We store the *trimmed* body (what the
      // backend actually saw) so retry sends an identical request.
      lastUserPromptRef.current = trimmed;
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
      setPendingFiles([]);
      setAttachError(null);
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

      // Build a multipart form so the same wire shape carries text
      // and any drag-dropped attachments. Always-multipart keeps the
      // backend on one contract — see /api/chat/stream proxy for the
      // legacy-JSON fallback.
      const form = new FormData();
      form.set("workspace_id", workspaceId);
      form.set("body", trimmed);
      if (opts.forceNewThread) {
        form.set("force_new_thread", "true");
      }
      for (const f of pendingFiles) {
        form.append("files", f, f.name);
      }
      try {
        const res = await fetch("/api/chat/stream", {
          method: "POST",
          body: form,
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
    [handleEvent, pendingFiles, streaming, workspaceId],
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

  // Cancel the in-flight stream. ``consumeSSE`` will throw
  // ``AbortError``; ``send``'s catch silently swallows that and the
  // ``finally`` clause clears the streaming flags. We also clear
  // them eagerly here so the UI flips back to the send button
  // without waiting for the fetch to unwind, and release the
  // runway spacer with the same scrollTop-preservation dance the
  // ``end`` / ``error`` handlers use.
  const stopStreaming = useCallback(() => {
    if (!abortRef.current) return;
    abortRef.current.abort();
    setStreaming(false);
    setAwaitingFirstDelta(false);
    releaseSpacer();
  }, [releaseSpacer]);

  // "Give me a different answer" — drops the last user turn and
  // everything after it locally, then re-issues ``send`` which will
  // push a fresh optimistic user segment + new assistant. Without
  // dropping the kept user segment too we'd render two identical
  // "You" rows back-to-back during the live regenerate.
  //
  // Trade-off: the previous user + assistant rows stay persisted on
  // the backend, so a reload shows both the original turn and the
  // regenerate turn (in arrival order). No regenerate API exists
  // on the backend; treating the live view as authoritative and
  // letting the durable history record both attempts is the
  // cheapest honest behavior.
  const handleRegenerate = useCallback(() => {
    const prompt = lastUserPromptRef.current;
    if (!prompt || streaming) return;
    setSegments((prev) => {
      const idx = findLastIndex(prev, (s) => s.kind === "user");
      if (idx < 0) return prev;
      return prev.slice(0, idx);
    });
    void send(prompt);
  }, [send, streaming]);

  // Keyboard shortcuts (window-scoped):
  //   • Esc       → abort streaming, or clear draft when idle
  //   • ⌘/Ctrl+K  → focus the composer
  //
  // We let the event pass through when focus is on something
  // outside the chat surface (e.g. a sidebar input) — checked by
  // testing whether ``document.activeElement`` is the body or
  // lives inside the chat root.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      const target = document.activeElement;
      const inChat =
        !target ||
        target === document.body ||
        (chatRootRef.current?.contains(target) ?? false);
      if (!inChat) return;
      if (e.key === "Escape") {
        if (streaming) {
          e.preventDefault();
          stopStreaming();
        } else if (draft.length > 0) {
          e.preventDefault();
          setDraft("");
        }
        return;
      }
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        if (streaming) return;
        e.preventDefault();
        textareaRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [streaming, draft, stopStreaming]);

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
      setDraftingSuggestion(null);
      setRetrievedByUserId({});
      lastUserIdRef.current = null;
      streamingIdRef.current = null;
      setRevealed({});
      setBottomSpacerPx(0);
    },
    [streaming, workspaceId],
  );

  const userSegmentCount = useMemo(
    () => segments.reduce((n, s) => (s.kind === "user" ? n + 1 : n), 0),
    [segments],
  );
  // Friendly header title. The backend creates threads with the
  // placeholder ``"New conversation"`` and never auto-renames, so
  // we derive a short title from the first user message once
  // there's one to look at. ``topic_summary`` (set when the agent
  // packs an outgoing thread) wins over both because it's the
  // canonical short description. Empty thread → keep the
  // placeholder so the slot doesn't collapse.
  const headerTitle = useMemo(() => {
    if (current.topic_summary && current.topic_summary.trim()) {
      return current.topic_summary.trim();
    }
    const explicit =
      current.title && current.title !== "New conversation"
        ? current.title.trim()
        : "";
    if (explicit) return explicit;
    const firstUser = segments.find(
      (s): s is UserSegment => s.kind === "user",
    );
    if (firstUser) {
      const flat = firstUser.body.replace(/\s+/g, " ").trim();
      if (flat.length <= 60) return flat;
      return flat.slice(0, 57).trimEnd() + "…";
    }
    return current.title || "New conversation";
  }, [current.title, current.topic_summary, segments]);
  /**
   * Slice :data:`segments` into render-ready rows. Tool segments get
   * folded into the **last** ``assistant_text`` of the same turn — a
   * "turn" being everything between two user messages. The render
   * loop maps these directly; the disclosure on the assistant row
   * surfaces tool details on demand.
   */
  const renderRows = useMemo(() => buildRenderRows(segments), [segments]);
  const lastUserIndex = useMemo(() => {
    for (let i = renderRows.length - 1; i >= 0; i--) {
      const row = renderRows[i];
      if (row.kind === "user") return row.userIndex;
    }
    return -1;
  }, [renderRows]);
  /**
   * Backwards-compat for :data:`currentTurnLastTool` below: the
   * indicator-state machine still derives "active tool" off the raw
   * segment array. Find the index of the last user *segment* (not
   * row) so the existing scan logic keeps working without churn.
   */
  const lastUserSegmentIndex = useMemo(() => {
    for (let i = segments.length - 1; i >= 0; i--) {
      if (segments[i].kind === "user") return i;
    }
    return -1;
  }, [segments]);
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
    for (let i = segments.length - 1; i > lastUserSegmentIndex; i--) {
      const s = segments[i];
      if (s.kind === "tool") return s;
    }
    return null;
  }, [segments, lastUserSegmentIndex]);
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

  // Regenerate is offered only when the previous turn settled
  // cleanly — i.e. we're idle, the last segment is a finalized
  // assistant_text, and we still remember the prompt that produced
  // it. (``ChatErrorCard``'s Retry button covers the failure case.)
  const canRegenerate = useMemo(() => {
    if (streaming) return false;
    if (!lastUserPromptRef.current) return false;
    const last = segments[segments.length - 1];
    return !!last && last.kind === "assistant_text" && !last.streaming;
  }, [segments, streaming]);

  // Show the "Jump to latest" pill once the user has scrolled
  // > 120px away from the bottom and the thread has *something*
  // worth jumping to. Empty thread = no point.
  const showJumpButton =
    scrollGap > 120 && (streaming || segments.length > 0);

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
    <div
      ref={chatRootRef}
      className="flex h-[calc(100vh-12rem)] min-h-[34rem] flex-col"
    >
      <div className="flex items-center gap-3 pb-2">
        <h2 className="truncate text-sm font-semibold text-white/90">
          {headerTitle}
        </h2>
        <span className="text-[11px] text-white/35">
          {current.status === "active" ? "live" : "archived"} ·{" "}
          {userSegmentCount} msg
        </span>
        {current.intent === "shape_project" ? (
          // E20-3 — explicit drafting badge so the user always sees
          // the mode (and a quick exit affordance if they didn't
          // realise the thread had flipped).
          <button
            type="button"
            data-testid="drafting-mode-badge"
            onClick={async () => {
              if (streaming || draftingFlipPending) return;
              setDraftingFlipPending(true);
              try {
                const resp = await fetch("/api/chat/intent", {
                  method: "POST",
                  headers: { "content-type": "application/json" },
                  body: JSON.stringify({
                    workspace_id: workspaceId,
                    intent: null,
                  }),
                });
                if (resp.ok) {
                  const updated = (await resp.json()) as Thread;
                  setCurrent(updated);
                }
              } finally {
                setDraftingFlipPending(false);
              }
            }}
            disabled={streaming || draftingFlipPending}
            title="Click to exit drafting mode"
            className="rounded-full bg-violet-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-200/90 transition hover:bg-violet-500/25 disabled:cursor-not-allowed disabled:opacity-40"
          >
            drafting ✕
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => resetConversation({})}
          className="ml-auto text-[11px] text-white/50 transition hover:text-white/90"
          disabled={streaming}
        >
          new conversation ↻
        </button>
      </div>

      {/* Banner is deferred until the turn settles. Mounting it
          mid-stream pushed the entire scroll content down by ~32px
          and felt like a jerk. The decision still arrives over SSE
          mid-turn — we just hold off rendering until streaming
          ends so the user sees a clean prompt → reply → suggestion
          sequence instead of an interruption. */}
      {shift && !streaming ? (
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

      {/* E20-3 — drafting-intent CTA. Same deferral logic as the
          topic-shift banner: wait for the turn to finish so the
          user doesn't see the suggestion jump in mid-stream. */}
      {draftingSuggestion && !streaming ? (
        <DraftingIntentBanner
          verdict={draftingSuggestion.verdict}
          reason={draftingSuggestion.reason}
          suggestedTitle={draftingSuggestion.suggestedTitle}
          pending={draftingFlipPending}
          onAccept={async () => {
            setDraftingFlipPending(true);
            try {
              const resp = await fetch("/api/chat/intent", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({
                  workspace_id: workspaceId,
                  intent:
                    draftingSuggestion.verdict === "ENTER"
                      ? "shape_project"
                      : null,
                }),
              });
              if (resp.ok) {
                const updated = (await resp.json()) as Thread;
                setCurrent(updated);
                setDraftingSuggestion(null);
              }
            } finally {
              setDraftingFlipPending(false);
            }
          }}
          onDismiss={() => setDraftingSuggestion(null)}
        />
      ) : null}

      <div className="relative flex min-h-0 flex-1 flex-col">
        <div ref={scrollerRef} className="flex-1 overflow-y-auto py-4">
          <ChoiceProvider onChoose={(label) => void send(label)}>
            {segments.length === 0 ? (
              <EmptyHint hasArchivedChats={hasArchivedChats} />
            ) : (
              <div className="space-y-6">
                {/* Single ordered timeline. Tool segments are NOT
                    rendered inline — tool activity surfaces through
                    the single-line ``TurnStatusSlot`` while running,
                    and through a per-turn ``Show details`` disclosure
                    attached to the last assistant_text once the
                    turn settles. Per-turn grouping = "everything
                    between two user messages": all tools that fired
                    inside a turn collapse under that turn's final
                    prose segment, so reading flow stays clean.
                    See :func:`buildRenderRows` for the slicing. */}
                {renderRows.map((row) => {
                  if (row.kind === "user") {
                    return (
                      <UserRow
                        key={row.segment.id}
                        segment={row.segment}
                        anchorRef={
                          row.userIndex === lastUserIndex
                            ? lastUserAnchorRef
                            : null
                        }
                        retrieved={
                          retrievedByUserId[
                            row.segment.serverId ?? row.segment.id
                          ] ?? null
                        }
                      />
                    );
                  }
                  return (
                    <AssistantTextRow
                      key={row.segment.id}
                      segment={row.segment}
                      revealLen={
                        revealMap.get(row.segment.id) ?? row.segment.body.length
                      }
                      tools={row.tools}
                    />
                  );
                })}
                {canRegenerate ? (
                  <div className="-mt-2">
                    <button
                      type="button"
                      onClick={handleRegenerate}
                      className="text-[11px] text-white/40 transition hover:text-white/80"
                    >
                      Regenerate ↻
                    </button>
                  </div>
                ) : null}
                {/* Single-line turn status at the end of the turn
                    (below the streamed prose if there is any, below
                    the user prompt if not). Replaces itself as state
                    changes — never stacks. The slot is a fixed-height
                    reservation while a turn is in flight so flips
                    between Thinking → Calling X → (gone) don't jiggle
                    the conversation vertically; outside of streaming
                    it collapses to nothing. */}
                <TurnStatusSlot status={turnStatus} streaming={streaming} />
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
        <button
          type="button"
          onClick={() => {
            const el = scrollerRef.current;
            if (!el) return;
            el.scrollTo({
              top: el.scrollHeight - el.clientHeight,
              behavior: "smooth",
            });
          }}
          aria-hidden={!showJumpButton}
          tabIndex={showJumpButton ? 0 : -1}
          className={`pointer-events-auto absolute bottom-4 right-4 rounded-full border border-white/15 bg-black/55 px-3 py-1.5 text-[11px] font-semibold text-white/80 backdrop-blur-md transition-opacity duration-150 hover:border-aqua/40 hover:text-white ${
            showJumpButton ? "opacity-100" : "pointer-events-none opacity-0"
          }`}
        >
          Jump to latest ↓
        </button>
      </div>

      {errorText ? (
        <ChatErrorCard
          error={errorText}
          onDismiss={() => setErrorText(null)}
          onRetry={() => {
            const last = lastUserPromptRef.current;
            setErrorText(null);
            void send(last ?? "");
          }}
          canRetry={!streaming && (lastUserPromptRef.current?.length ?? 0) > 0}
        />
      ) : null}

      <form
        onSubmit={onSubmit}
        onDragOver={(e) => {
          // Only react to file drops — bail on text-only drags so we
          // don't fight the textarea's native rich-text drop.
          if (Array.from(e.dataTransfer.types).includes("Files")) {
            e.preventDefault();
          }
        }}
        onDrop={(e) => {
          if (!Array.from(e.dataTransfer.types).includes("Files")) return;
          e.preventDefault();
          acceptFiles(Array.from(e.dataTransfer.files));
        }}
        className="flex flex-col gap-1 border-t border-white/5 pt-3"
      >
        {pendingFiles.length > 0 ? (
          <ul className="flex flex-wrap gap-1.5">
            {pendingFiles.map((f, idx) => (
              <li
                key={`${f.name}-${idx}-${f.size}`}
                className="flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] text-white/75"
              >
                <span aria-hidden>
                  {f.type.startsWith("image/")
                    ? "🖼"
                    : f.type === "application/pdf"
                      ? "📄"
                      : "📎"}
                </span>
                <span className="max-w-[180px] truncate" title={f.name}>
                  {f.name}
                </span>
                <span className="text-white/40">
                  {(f.size / 1024).toFixed(0)} KB
                </span>
                <button
                  type="button"
                  onClick={() => removePendingFile(idx)}
                  aria-label={`Remove ${f.name}`}
                  className="text-white/40 transition hover:text-rose-300"
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {attachError ? (
          <p className="text-[11px] text-rose-300/80">{attachError}</p>
        ) : null}
        <div className="flex items-end gap-2">
          <label
            className="cursor-pointer text-sm text-white/40 transition hover:text-white"
            title="Attach an image, PDF, or text file"
          >
            📎
            <input
              type="file"
              multiple
              accept="image/jpeg,image/png,image/webp,image/gif,application/pdf,text/markdown,text/plain,text/csv,application/json,application/yaml,application/x-yaml"
              className="hidden"
              onChange={(e) => {
                if (e.target.files) acceptFiles(Array.from(e.target.files));
                // Allow re-picking the same file after removing it.
                e.target.value = "";
              }}
            />
          </label>
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask the agent. Enter to send, Shift-Enter for newline. Drag files to attach."
            rows={2}
            disabled={streaming}
            className="min-h-[48px] flex-1 resize-none bg-transparent px-1 py-2 text-sm text-white placeholder-white/25 focus:outline-none disabled:opacity-50"
          />
          {streaming ? (
            <button
              type="button"
              onClick={stopStreaming}
              aria-label="Stop streaming"
              className="text-sm font-semibold text-rose-300 transition hover:text-rose-200"
            >
              stop ✕
            </button>
          ) : (
            <button
              type="submit"
              disabled={draft.trim().length === 0 && pendingFiles.length === 0}
              className="text-sm font-semibold text-aqua transition hover:text-aqua/80 disabled:cursor-not-allowed disabled:text-white/25"
            >
              send ↵
            </button>
          )}
        </div>
        {!streaming && draft.length === 0 ? (
          <span className="text-[10px] text-white/30">
            Enter to send · Shift+Enter newline · ⌘K to focus · Esc to cancel
          </span>
        ) : null}
      </form>
    </div>
  );
}

function EmptyHint({ hasArchivedChats }: { hasArchivedChats: boolean }) {
  return (
    <div className="flex h-full items-center justify-center text-center text-[12px] text-white/35">
      <div>
        <p className="font-semibold text-white/60">Ask Ship anything about this workspace.</p>
        <p className="mt-1 max-w-sm">
          It can pull up Inbox items, walk you through a Play, kick off a
          Run, or surface what your repos look like under the hood. Try
          asking <em className="not-italic text-white/55">&ldquo;what&rsquo;s in my inbox?&rdquo;</em>{" "}
          or <em className="not-italic text-white/55">&ldquo;list active automations&rdquo;</em>.
        </p>
        {hasArchivedChats ? (
          <p className="mt-3">
            <Link
              href="/chat/archived"
              className="text-white/45 hover:text-white"
            >
              Looking for an older thread? View archived chats →
            </Link>
          </p>
        ) : null}
      </div>
    </div>
  );
}

const UserRow = memo(function UserRow({
  segment,
  anchorRef,
  retrieved,
}: {
  segment: UserSegment;
  anchorRef: React.RefObject<HTMLDivElement | null> | null;
  retrieved: RetrievedContext | null;
}) {
  return (
    <div ref={anchorRef ?? undefined} className="text-[14px] leading-relaxed">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-aqua/80">
        You
      </div>
      <ChatMarkdown text={segment.body} animate={false} />
      {retrieved && retrieved.hits.length > 0 ? (
        <RetrievedContextDisclosure retrieved={retrieved} />
      ) : null}
    </div>
  );
});

/**
 * Soft "Using N prior memories" disclosure rendered below a user
 * message when the backend's per-turn retrieval pulled in any
 * packed-conversation summaries. Collapsed by default — power
 * users can expand to see which buckets the agent leaned on. Each
 * row deeplinks into the bucket page for follow-up.
 */
function RetrievedContextDisclosure({
  retrieved,
}: {
  retrieved: RetrievedContext;
}) {
  const n = retrieved.hits.length;
  const label = n === 1 ? "1 prior memory" : `${n} prior memories`;
  return (
    <details className="mt-2 group/ret">
      <summary className="cursor-pointer text-[10px] uppercase tracking-[0.14em] text-white/30 transition hover:text-white/60 list-none [&::-webkit-details-marker]:hidden">
        <span className="inline-flex items-center gap-1">
          <span aria-hidden className="transition-transform group-open/ret:rotate-90">
            ▸
          </span>
          <span>Using {label}</span>
        </span>
      </summary>
      <ul className="mt-1.5 space-y-1 pl-4 text-[11px] text-white/55">
        {retrieved.hits.map((h, i) => (
          <li key={`${h.bucket_slug}-${i}`} className="flex items-start gap-2">
            <span className="mt-0.5 text-white/30">·</span>
            <span className="flex-1">
              {h.bucket_slug ? (
                <Link
                  href={`/knowledge/${encodeURIComponent(h.bucket_slug)}`}
                  className="text-white/65 hover:text-white"
                >
                  {h.bucket_name || h.bucket_slug}
                </Link>
              ) : (
                <span className="text-white/65">{h.bucket_name}</span>
              )}{" "}
              <span className="text-white/35">— {h.title}</span>
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

const AssistantTextRow = memo(function AssistantTextRow({
  segment,
  revealLen,
  tools,
}: {
  segment: AssistantTextSegment;
  revealLen: number;
  tools: ToolSegment[];
}) {
  const sliced = segment.body.slice(
    0,
    Math.max(0, Math.min(revealLen, segment.body.length)),
  );
  // While streaming, soft-close any inline emphasis / code / link
  // delimiters that the model has opened but not yet closed. Without
  // this, "abc *def" parses as plain text → "abc *def*" suddenly
  // re-parses as emphasis once the closer arrives, and the user
  // sees the trailing word visibly re-flow. Closing the delimiter
  // optimistically keeps the structure stable as more chars arrive.
  const displayBody = segment.streaming
    ? balanceStreamingMarkdown(sliced)
    : sliced;
  // Disclosure is hidden while the assistant is still streaming —
  // the single-line ``TurnStatusSlot`` is the live progress signal,
  // and showing tool details mid-stream would distract from the
  // prose flow. Once the turn settles we surface the count + a
  // ``<details>`` toggle so curious users can see what ran.
  const showTools = !segment.streaming && tools.length > 0;
  return (
    <div className="group relative text-[14px] leading-relaxed">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-lilac/80">
        Ship
      </div>
      <ChatMarkdown text={displayBody} animate={segment.animate} />
      {showTools ? <TurnToolsDisclosure tools={tools} /> : null}
      {segment.streaming ? null : (
        <CopyMessageButton text={segment.body} />
      )}
    </div>
  );
});

/**
 * Pad a still-streaming markdown body so unclosed inline runs render
 * with the same structure they'll have once their closer arrives.
 *
 * Three classes flip the AST when their closer lands and would
 * otherwise visibly re-flow the rendered text mid-stream:
 *
 * 1. Fenced code blocks (``\`\`\``). If a fence is open we close it
 *    and skip the inline rules — everything after the fence opener
 *    is opaque code anyway.
 * 2. Inline code spans (single `` ` ``).
 * 3. Bold (``**`` and ``__``).
 *
 * Plain italic ``*foo*`` is intentionally NOT balanced — single-star
 * heuristics are noisy ("* item" list markers, mid-word ``2*x``)
 * and over-correcting causes its own flicker. The bold case is the
 * one users actually feel.
 *
 * Heuristic, not a real parser. Bails out for bodies > 8 KB so this
 * never becomes a CPU sink on long replies.
 */
function balanceStreamingMarkdown(body: string): string {
  if (body.length === 0 || body.length > 8192) return body;
  const fenceMatches = body.match(/(^|\n)```/g);
  const fenceOpen = fenceMatches ? fenceMatches.length % 2 === 1 : false;
  if (fenceOpen) {
    return body + "\n```";
  }
  let codeTicks = 0;
  for (let i = 0; i < body.length; i++) {
    if (body[i] === "`") codeTicks++;
  }
  let out = body;
  if (codeTicks % 2 === 1) out += "`";
  const boldStar = (out.match(/\*\*/g) ?? []).length;
  if (boldStar % 2 === 1) out += "**";
  const boldUnder = (out.match(/__/g) ?? []).length;
  if (boldUnder % 2 === 1) out += "__";
  return out;
}

/**
 * Per-turn `<details>` summary that hides the tool calls behind a
 * single line so the conversation stays clean. Renders nothing when
 * the turn had no tools. Uses the existing :func:`renderToolResult`
 * registry so individual tool cards keep their rich payloads, just
 * tucked away by default.
 */
function TurnToolsDisclosure({ tools }: { tools: ToolSegment[] }) {
  if (tools.length === 0) return null;
  const label = tools.length === 1 ? "1 tool call" : `${tools.length} tool calls`;
  return (
    <details className="mt-3 group/disc">
      <summary className="cursor-pointer text-[11px] text-white/35 transition hover:text-white/70 list-none [&::-webkit-details-marker]:hidden">
        <span className="inline-flex items-center gap-1">
          <span aria-hidden className="transition-transform group-open/disc:rotate-90">
            ▸
          </span>
          <span>Show details · {label}</span>
        </span>
      </summary>
      <div className="mt-3 space-y-3">
        {tools.map((t) => (
          <ToolSegmentRow key={t.id} segment={t} />
        ))}
      </div>
    </details>
  );
}

/**
 * Inline "Copy" affordance attached to a finalized assistant
 * segment. Faint by default, opaque on row hover (driven by the
 * Tailwind ``group``/``group-hover`` pair on the parent row).
 * Clipboard failures (denied permission, insecure context) flash
 * a brief "Copy failed" so the user knows nothing happened.
 */
function CopyMessageButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timerRef = useRef<number | null>(null);
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);
  const onClick = async (e: ReactMouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    try {
      if (typeof navigator === "undefined" || !navigator.clipboard) {
        throw new Error("clipboard unavailable");
      }
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setState("idle"), 1500);
  };
  const label =
    state === "copied"
      ? "Copied ✓"
      : state === "failed"
        ? "Copy failed"
        : "Copy";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Copy reply"
      className="absolute right-0 top-0 inline-flex items-center gap-1 rounded border border-white/5 bg-transparent px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/35 opacity-0 transition hover:border-white/15 hover:bg-white/[0.04] hover:text-white/85 group-hover:opacity-100 focus:opacity-100"
    >
      <span aria-hidden>📋</span>
      <span>{label}</span>
    </button>
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

/**
 * Fixed-height status slot below the timeline.
 *
 * Replaces the conditional ``{turnStatus ? <TurnStatusLine /> :
 * null}`` render path that flickered the layout up/down on every
 * Thinking → Calling X → Thinking → gone transition. The slot
 * always reserves ``min-h-[20px]`` while a turn is streaming, so
 * the conversation stays vertically stable while the status text
 * fades in/out via the Tailwind opacity transition.
 *
 * Outside of streaming we render nothing — we don't want a
 * permanent blank gap below historical turns.
 */
function TurnStatusSlot({
  status,
  streaming,
}: {
  status: { tone: "shimmer" | "error"; text: string } | null;
  streaming: boolean;
}) {
  if (!streaming) return null;
  if (!status) {
    return <div aria-hidden className="min-h-[20px]" />;
  }
  const cls =
    status.tone === "shimmer"
      ? "chat-shimmer text-[13px]"
      : "text-[13px] text-rose-300/80";
  return (
    <div
      className={`min-h-[20px] opacity-100 transition-opacity duration-150 ${cls}`}
    >
      {status.text}
    </div>
  );
}

/**
 * Inline error card for stream/transport failures, replacing the
 * old muted-rose single-line ``errorText`` strip.
 *
 * Mirrors the ``<details>`` collapse pattern from
 * :class:`tool-renderers/index.tsx::ErrorCard` so the chat UX
 * stays consistent (rose dot · headline · collapsed detail), but
 * deliberately doesn't extend that component — that one is
 * tool-scoped and we don't want to twist its API for a non-tool
 * use case. The card lives just above the composer; the composer
 * stays enabled so the user can retry, dismiss, or just type
 * something new without first having to clear the error.
 */
function ChatErrorCard({
  error,
  onDismiss,
  onRetry,
  canRetry,
}: {
  error: string;
  onDismiss: () => void;
  onRetry: () => void;
  canRetry: boolean;
}) {
  // The headline is intentionally generic — the underlying message
  // (HTTP status, exception text, etc.) lives in the <details>
  // payload below and is rarely useful on its own.
  const headline = "Stream failed";
  const isMultiline = /\r?\n/.test(error);
  const firstLine = error.split(/\r?\n/, 1)[0] ?? error;
  const shortMessage =
    firstLine.length > 120 ? firstLine.slice(0, 119).trimEnd() + "…" : firstLine;
  const needsDetails =
    error.length > 200 || isMultiline || error.length > shortMessage.length;
  return (
    <div className="my-2 rounded-2xl border border-rose-400/30 bg-rose-500/[0.06] px-4 py-3 text-[12px] text-rose-100/95">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-300"
        />
        <span className="font-semibold">{headline}</span>
        <span className="min-w-0 flex-1 truncate text-rose-200/80">
          {shortMessage}
        </span>
        <button
          type="button"
          onClick={onRetry}
          disabled={!canRetry}
          className="text-[11px] font-semibold text-rose-100 hover:text-white disabled:cursor-not-allowed disabled:text-rose-200/40"
        >
          Retry
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="text-[11px] text-rose-200/70 hover:text-white"
        >
          Dismiss
        </button>
      </div>
      {needsDetails ? (
        <details className="group mt-2">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[11px] opacity-70 hover:opacity-100">
            <span
              aria-hidden
              className="inline-block transition-transform group-open:rotate-90"
            >
              ▸
            </span>
            <span>show details</span>
          </summary>
          <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-black/40 p-2.5 text-[11px] leading-relaxed">
            {error}
          </pre>
        </details>
      ) : null}
    </div>
  );
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
  // Explicit phrase ("let's switch" / "теперь другое") → confident,
  // direct copy + "switch now" CTA. Otherwise the LLM classifier
  // hedges, so we phrase the banner as a soft suggestion the user
  // can dismiss without feeling pushed.
  const headline = shift.explicitPhrase ? "Switching topics." : "Topic shift.";
  const reason = shift.explicitPhrase
    ? "Got it — let's pack the current thread before we move on."
    : (shift.reason ?? "Looks like you moved on.");
  const cta = shift.explicitPhrase ? "switch now" : "pack & start fresh";
  return (
    <div className="flex items-start gap-3 py-2 text-[12px] text-amber-200/90">
      <span className="mt-0.5 h-1 w-1 shrink-0 rounded-full bg-amber-300" />
      <div className="flex-1">
        <strong className="font-semibold">{headline}</strong> {reason} Pack this
        thread into{" "}
        <code className="text-amber-100">{bucketName ?? "a new bucket"}</code>?
        <button
          type="button"
          onClick={onPack}
          className="ml-2 font-semibold text-amber-100 underline-offset-2 hover:underline"
        >
          {cta}
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
 * E20-3 — inline "Switch to drafting" / "Exit drafting" CTA.
 *
 * Backend's drafting-intent classifier (E20-2) emits a verdict per
 * user turn; we render this banner once the turn settles. Click
 * flips ``thread.intent`` in place via POST /chat/active/intent
 * (E20-1) — no archive, no fresh thread.
 */
function DraftingIntentBanner({
  verdict,
  reason,
  suggestedTitle,
  pending,
  onAccept,
  onDismiss,
}: {
  verdict: "ENTER" | "EXIT";
  reason: string;
  suggestedTitle: string | null;
  pending: boolean;
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const isEnter = verdict === "ENTER";
  const headline = isEnter ? "Shape a new project?" : "Exit drafting?";
  const cta = isEnter ? "switch to drafting" : "exit drafting";
  const subline =
    reason ||
    (isEnter
      ? "Looks like you'd like to shape something new — switch this thread into drafting mode?"
      : "Sounds like you want to move on — step out of drafting?");
  return (
    <div
      className="flex items-start gap-3 py-2 text-[12px] text-violet-200/90"
      data-testid="drafting-intent-banner"
      data-verdict={verdict}
    >
      <span className="mt-0.5 h-1 w-1 shrink-0 rounded-full bg-violet-300" />
      <div className="flex-1">
        <strong className="font-semibold">{headline}</strong> {subline}
        {isEnter && suggestedTitle ? (
          <>
            {" "}
            Suggested title:{" "}
            <code className="text-violet-100">{suggestedTitle}</code>.
          </>
        ) : null}
        <button
          type="button"
          onClick={onAccept}
          disabled={pending}
          className="ml-2 font-semibold text-violet-100 underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:opacity-40"
        >
          {pending ? "flipping…" : cta}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          disabled={pending}
          className="ml-2 text-white/40 hover:text-white/80 disabled:cursor-not-allowed disabled:opacity-40"
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
