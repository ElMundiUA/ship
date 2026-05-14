/**
 * Test helpers for the Navigator memory suite (E17).
 *
 * The suite runs against a dedicated workspace (``E2E_NAVIGATOR_WORKSPACE_ID``)
 * provisioned by ``tools/scripts/setup_e2e_navigator_workspace.py``:
 * two service users with PATs (``primary`` + ``secondary``) so we can
 * exercise both single-user happy paths AND the cross-user isolation
 * boundary that ELS-130's "personal scope" promise hinges on.
 *
 * Why the helpers carry their own ``base`` + ``token`` instead of
 * using ``e2e/lib/ship-api.ts``: the existing helpers default to the
 * operator's prod workspace (``denys-99938640``). The memory tests
 * deliberately point at the isolated e2e workspace + service-user
 * PATs so we don't pollute the operator's real mem0 store or trip
 * on facts already there.
 */

import type { APIRequestContext } from "@playwright/test";


// ---------------------------------------------------------------------------
// Credentials wiring
// ---------------------------------------------------------------------------


export function memorySuiteEnv() {
  const base = process.env.E2E_SHIP_API_BASE?.trim().replace(/\/+$/, "");
  const workspaceId = process.env.E2E_NAVIGATOR_WORKSPACE_ID?.trim();
  const primaryPat = process.env.E2E_NAVIGATOR_PAT_PRIMARY?.trim();
  const secondaryPat = process.env.E2E_NAVIGATOR_PAT_SECONDARY?.trim();
  return {
    base: base || null,
    workspaceId: workspaceId || null,
    primaryPat: primaryPat || null,
    secondaryPat: secondaryPat || null,
  };
}


export function hasMemorySuiteCredentials(): boolean {
  const env = memorySuiteEnv();
  return Boolean(env.base && env.workspaceId && env.primaryPat);
}


// ---------------------------------------------------------------------------
// API wrappers
// ---------------------------------------------------------------------------


export interface NavigatorMemory {
  id: string;
  fact_text: string;
  project_native_id: string | null;
  intent_at_capture: string | null;
  source_thread_id: string | null;
  source_message_id: string | null;
  source_message_position: number | null;
  confidence: number;
  created_at: string;
  updated_at: string;
}


export interface NavigatorMemoryHealth {
  facts_count: number;
  adds_24h: number;
  add_failures_24h: number;
  searches_24h: number;
  search_failures_24h: number;
  zero_hit_rate_24h: number;
}


export interface AuthCtx {
  base: string;
  token: string;
  workspaceId: string;
}


export async function listMemories(
  request: APIRequestContext,
  ctx: AuthCtx,
  options: {
    projectNativeId?: string | "untagged";
    limit?: number;
    offset?: number;
  } = {},
): Promise<{ items: NavigatorMemory[]; total: number; status: number }> {
  const params = new URLSearchParams();
  if (options.projectNativeId)
    params.set("project_native_id", options.projectNativeId);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.offset !== undefined)
    params.set("offset", String(options.offset));
  const qs = params.toString();
  const res = await request.get(
    `${ctx.base}/v1/workspaces/${encodeURIComponent(
      ctx.workspaceId,
    )}/navigator-memories${qs ? `?${qs}` : ""}`,
    {
      headers: {
        Authorization: `Bearer ${ctx.token}`,
        Accept: "application/json",
      },
    },
  );
  if (!res.ok()) {
    return { items: [], total: 0, status: res.status() };
  }
  const body = (await res.json()) as {
    items: NavigatorMemory[];
    total: number;
  };
  return { ...body, status: res.status() };
}


export async function deleteMemory(
  request: APIRequestContext,
  ctx: AuthCtx,
  memoryId: string,
): Promise<number> {
  const res = await request.delete(
    `${ctx.base}/v1/workspaces/${encodeURIComponent(
      ctx.workspaceId,
    )}/navigator-memories/${encodeURIComponent(memoryId)}`,
    {
      headers: { Authorization: `Bearer ${ctx.token}` },
    },
  );
  return res.status();
}


export async function bulkForget(
  request: APIRequestContext,
  ctx: AuthCtx,
  days: number,
): Promise<{ deleted: number; status: number }> {
  const res = await request.post(
    `${ctx.base}/v1/workspaces/${encodeURIComponent(
      ctx.workspaceId,
    )}/navigator-memories/forget`,
    {
      headers: {
        Authorization: `Bearer ${ctx.token}`,
        "Content-Type": "application/json",
      },
      data: JSON.stringify({ days }),
    },
  );
  if (!res.ok()) return { deleted: 0, status: res.status() };
  const body = (await res.json()) as { deleted: number };
  return { ...body, status: res.status() };
}


export async function fetchHealth(
  request: APIRequestContext,
  ctx: AuthCtx,
): Promise<NavigatorMemoryHealth | { status: number }> {
  const res = await request.get(
    `${ctx.base}/v1/workspaces/${encodeURIComponent(
      ctx.workspaceId,
    )}/navigator-memories/health`,
    {
      headers: {
        Authorization: `Bearer ${ctx.token}`,
        Accept: "application/json",
      },
    },
  );
  if (!res.ok()) return { status: res.status() };
  return (await res.json()) as NavigatorMemoryHealth;
}


// ---------------------------------------------------------------------------
// Mutators — used to seed memories without burning OpenAI tokens.
// ---------------------------------------------------------------------------


/**
 * Force-create a fact directly via the internal sandbox endpoint.
 *
 * We don't go through the LLM extractor for seeding — it's slow, costs
 * money, and is exactly what the ELS-127 unit suite already pins. For
 * the e2e suite we want deterministic content so assertions can match
 * on the fact's text. The sandbox endpoint mirrors what the per-message
 * extractor would have written, including the audit trail.
 *
 * Falls back to "send a chat message and wait" only when the sandbox
 * endpoint is gated off (production hardening).
 */
export async function seedMemory(
  request: APIRequestContext,
  ctx: AuthCtx,
  factText: string,
  options: {
    projectNativeId?: string | null;
    intentAtCapture?: string | null;
    confidence?: number;
  } = {},
): Promise<{ id: string; status: number }> {
  // The sandbox helper is mounted at ``/v1/workspaces/{ws}/navigator-memories/_test_seed``
  // and is gated behind ``SHIP_E2E_SANDBOX=true`` — see the route file
  // for the gating. When the flag is off (prod), the call returns 404
  // and the test should skip; that's why we don't hard-fail here.
  const res = await request.post(
    `${ctx.base}/v1/workspaces/${encodeURIComponent(
      ctx.workspaceId,
    )}/navigator-memories/_test_seed`,
    {
      headers: {
        Authorization: `Bearer ${ctx.token}`,
        "Content-Type": "application/json",
      },
      data: JSON.stringify({
        fact_text: factText,
        project_native_id: options.projectNativeId ?? null,
        intent_at_capture: options.intentAtCapture ?? null,
        confidence: options.confidence ?? 0.9,
      }),
    },
  );
  if (!res.ok()) return { id: "", status: res.status() };
  const body = (await res.json()) as { id: string };
  return { ...body, status: res.status() };
}


/**
 * Drain every memory the caller currently owns. Used in ``beforeEach``
 * to put the workspace in a known-empty state — the test then seeds
 * exactly the rows it needs.
 *
 * Implementation note: the bulk-forget endpoint caps at 90 days, so
 * a single call won't catch rows older than that. For the e2e suite
 * that ceiling is fine — the workspace was provisioned today. If we
 * ever leave rows around longer, switch to a paginated delete loop.
 */
export async function cleanAllMemories(
  request: APIRequestContext,
  ctx: AuthCtx,
): Promise<void> {
  await bulkForget(request, ctx, 90);
  // Belt-and-braces — anything bulk-forget missed (rows older than 90d
  // or seed rows added with a forward-dated created_at) we mop up
  // one-by-one. ``listMemories`` already filters by owner, so the
  // delete loop only touches our own rows.
  const list = await listMemories(request, ctx, { limit: 200 });
  for (const row of list.items) {
    await deleteMemory(request, ctx, row.id);
  }
}


// ---------------------------------------------------------------------------
// Polling helpers
// ---------------------------------------------------------------------------


/**
 * Poll the list endpoint until a fact whose text matches ``predicate``
 * appears, or the timeout elapses. We poll because per-message
 * extraction runs as a background task (``asyncio.create_task`` in
 * ``backend/app/api/v1/routes/chat.py:648``) — there's no synchronous
 * write path the test can latch onto.
 */
export async function waitForMemory(
  request: APIRequestContext,
  ctx: AuthCtx,
  predicate: (row: NavigatorMemory) => boolean,
  options: { timeoutMs?: number; pollMs?: number } = {},
): Promise<NavigatorMemory | null> {
  const timeoutMs = options.timeoutMs ?? 30_000;
  const pollMs = options.pollMs ?? 1_000;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const list = await listMemories(request, ctx, { limit: 200 });
    const hit = list.items.find(predicate);
    if (hit) return hit;
    await new Promise((r) => setTimeout(r, pollMs));
  }
  return null;
}
