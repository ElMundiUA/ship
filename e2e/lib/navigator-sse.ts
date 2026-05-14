/**
 * Minimal SSE reader for the Navigator stream.
 *
 * The backend's ``POST /v1/workspaces/{ws}/chat/stream`` emits
 * line-framed ``data: <json>`` events terminated by ``[DONE]`` —
 * see ``chat_stream`` in ``backend/app/api/v1/routes/chat.py``. The
 * client code that renders the response already understands the
 * shape (``delta`` for tokens, ``tool_use`` for tool invocations,
 * ``message`` for whole assistant turns), so for the e2e suite we
 * only need a small reader that surfaces:
 *
 *   - the concatenated assistant text (``delta`` events joined),
 *   - the list of tool names invoked during the turn,
 *   - the raw event stream when a test needs to assert on a
 *     specific event type (``recall`` / ``recall_context`` etc).
 *
 * The reader is deliberately permissive — unknown event types are
 * dropped so adding a new SSE shape on the backend doesn't break
 * the e2e suite.
 */

import type { APIRequestContext } from "@playwright/test";


export interface NavigatorStreamEvent {
  type?: string;
  text?: string;
  // Tool-mode events carry ``name`` (e.g. ``recall_context``) and
  // optional ``arguments`` JSON-encoded as ``string`` on the wire.
  name?: string;
  arguments?: unknown;
  // ``message`` events carry the full assistant turn for clients
  // that don't want to reconstruct from deltas.
  content?: unknown;
  // The server sometimes emits a top-level ``delta`` field instead
  // of ``text`` when the deltas came from a non-OpenAI provider —
  // keep both for forward-compat.
  delta?: string;
  [key: string]: unknown;
}


export interface NavigatorStreamResult {
  text: string;
  events: NavigatorStreamEvent[];
  toolNames: string[];
  status: number;
}


export interface NavigatorStreamOptions {
  base: string;
  token: string;
  workspaceId: string;
  body: string;
  classifyShift?: boolean;
  // Force-create a new thread before streaming — useful when a test
  // wants to assert "first-turn retrieval" semantics without leftover
  // chatter polluting the assistant context.
  freshThread?: boolean;
  timeoutMs?: number;
}


/**
 * Open a fresh Navigator thread when requested, then stream a single
 * user turn. Returns the parsed events + concatenated assistant text.
 *
 * The function never throws on a non-2xx — it returns the status so
 * the caller can soft-skip (e.g. on 412 when the backend has no LLM
 * key configured for the workspace).
 */
export async function streamNavigatorTurn(
  request: APIRequestContext,
  opts: NavigatorStreamOptions,
): Promise<NavigatorStreamResult> {
  const {
    base,
    token,
    workspaceId,
    body,
    classifyShift = true,
    freshThread = false,
    timeoutMs = 120_000,
  } = opts;
  const trimmed = base.replace(/\/+$/, "");
  if (freshThread) {
    await request.post(
      `${trimmed}/v1/workspaces/${encodeURIComponent(
        workspaceId,
      )}/chat/active/new`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        data: JSON.stringify({}),
      },
    );
  }

  const res = await request.post(
    `${trimmed}/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/stream`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      // ChatStreamIn shape — see ``backend/app/api/v1/routes/chat.py``.
      data: JSON.stringify({ body, classify_shift: classifyShift }),
      timeout: timeoutMs,
    },
  );
  const status = res.status();
  if (!res.ok()) {
    return { text: "", events: [], toolNames: [], status };
  }

  // Playwright's APIRequestContext does not expose a streaming reader,
  // so the entire response body is buffered. The backend's SSE
  // pipeline flushes only at the end of the turn for short replies,
  // which is fine for our assertions. For long turns the reader will
  // still see every event because ``res.text()`` reads to EOF.
  const raw = await res.text();
  const events: NavigatorStreamEvent[] = [];
  let text = "";
  const toolNames = new Set<string>();
  for (const line of raw.split(/\r?\n/)) {
    if (!line.startsWith("data:")) continue;
    const payload = line.slice(5).trim();
    if (!payload || payload === "[DONE]") continue;
    let parsed: NavigatorStreamEvent;
    try {
      parsed = JSON.parse(payload) as NavigatorStreamEvent;
    } catch {
      continue;
    }
    events.push(parsed);
    if (parsed.type === "delta" && typeof parsed.text === "string") {
      text += parsed.text;
    } else if (typeof parsed.delta === "string") {
      text += parsed.delta;
    } else if (
      parsed.type === "tool_call" ||
      parsed.type === "tool_use" ||
      parsed.type === "tool"
    ) {
      if (typeof parsed.name === "string") toolNames.add(parsed.name);
    }
  }
  return {
    text,
    events,
    toolNames: Array.from(toolNames),
    status,
  };
}
