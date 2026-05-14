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


/**
 * One tool invocation observed on the SSE wire.
 *
 * The backend's chat handler emits ``tool_call`` when the model
 * decides to invoke a tool, then ``tool_result`` with either
 * ``ok=true`` and a ``result`` payload, or ``ok=false`` and an
 * ``error`` string. We pair them up by ``id`` so the trajectory
 * analyser can spot retry-after-failure regressions: if the model
 * gets ``ok=false`` from a tool and the *next* event is another
 * ``tool_call``, the agent is silently retrying — that's either a
 * tool bug (the failure is recoverable but the agent shouldn't
 * have hit it) or a prompt bug (the agent doesn't know to escalate
 * to the user).
 */
export interface ToolInvocation {
  id: string;
  name: string;
  args: unknown;
  ok: boolean;
  error: string | null;
  result: unknown;
  // Index of the matching ``tool_call`` in the parent event array,
  // useful when callers want to inspect adjacent events.
  callIndex: number;
  resultIndex: number | null;
}


/**
 * Result of :func:`analyseToolTrajectory`. ``retryAfterFailure``
 * pairs spell out the regression the user asked us to flag:
 * "agent called X, got error, then immediately tried Y" — every
 * such occurrence is a tool or prompt bug we should fix.
 */
export interface ToolTrajectoryAnalysis {
  invocations: ToolInvocation[];
  retryAfterFailure: Array<{
    failed: ToolInvocation;
    retried: ToolInvocation;
  }>;
  // Tools that errored without any retry — agent surfaced the error
  // and stopped. These are not necessarily bugs but worth surfacing
  // for the test report.
  unrecoveredFailures: ToolInvocation[];
}


export function analyseToolTrajectory(
  events: NavigatorStreamEvent[],
): ToolTrajectoryAnalysis {
  // Build an id → call index map first so a tool_result can refer
  // back to its initiator regardless of how many deltas interleave.
  const callsById = new Map<string, ToolInvocation>();
  const invocations: ToolInvocation[] = [];
  events.forEach((evt, i) => {
    const e = evt as Record<string, unknown>;
    const type = typeof e.type === "string" ? e.type : "";
    if (type === "tool_call" || type === "tool_use" || type === "tool") {
      const id = typeof e.id === "string" ? e.id : `auto-${i}`;
      const inv: ToolInvocation = {
        id,
        name: typeof e.name === "string" ? e.name : "",
        args: e.arguments ?? e.args ?? null,
        ok: true,
        error: null,
        result: null,
        callIndex: i,
        resultIndex: null,
      };
      callsById.set(id, inv);
      invocations.push(inv);
    } else if (type === "tool_result") {
      const id = typeof e.id === "string" ? e.id : "";
      const match = callsById.get(id);
      if (!match) return;
      match.resultIndex = i;
      match.result = e.result ?? e.output ?? null;
      // Backend normalises to ``{ok: bool, result|error}``. Older
      // payloads carry ``ok`` only on success; defensively treat
      // a missing ``ok`` with an ``error`` field as a failure.
      const okFlag = typeof e.ok === "boolean" ? e.ok : undefined;
      const errorStr = typeof e.error === "string" ? e.error : null;
      if (okFlag === false || errorStr) {
        match.ok = false;
        match.error = errorStr ?? "tool failed without an error message";
      }
    }
  });

  const retryAfterFailure: Array<{
    failed: ToolInvocation;
    retried: ToolInvocation;
  }> = [];
  const unrecovered: ToolInvocation[] = [];
  invocations.forEach((inv, idx) => {
    if (inv.ok) return;
    const next = invocations[idx + 1];
    if (next) {
      retryAfterFailure.push({ failed: inv, retried: next });
    } else {
      unrecovered.push(inv);
    }
  });

  return {
    invocations,
    retryAfterFailure,
    unrecoveredFailures: unrecovered,
  };
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

  // ``/chat/stream`` switched to ``multipart/form-data`` in phase 3b
  // (attachments + text body in one request). Form fields:
  // ``body`` (required), ``classify_shift`` (optional bool), and
  // optional ``files[]``. JSON-body callers fail with 422 at the
  // form-validator gate.
  const res = await request.post(
    `${trimmed}/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/stream`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "text/event-stream",
      },
      multipart: {
        body,
        classify_shift: classifyShift ? "true" : "false",
      },
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
