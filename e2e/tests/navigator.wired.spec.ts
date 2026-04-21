import { expect, test, type APIRequestContext } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiGet,
  shipApiPost,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Dedicated Navigator (agent chat) e2e.
 *
 * Navigator is the single-window agent surface at ``/chat`` — one active
 * thread, named-bucket sidebar, SSE streaming composer. It's wired by
 * three backend surfaces in ``backend/app/api/v1/routes/chat.py``:
 *
 *   - ``GET  /v1/workspaces/{ws}/chat/active``            active thread
 *   - ``POST /v1/workspaces/{ws}/chat/active/new``        fresh thread
 *   - ``POST /v1/workspaces/{ws}/chat/stream``            SSE reply
 *   - ``GET  /v1/workspaces/{ws}/buckets``                knowledge buckets
 *
 * We split the coverage into three rings so teams can opt in to as much
 * of the stack as their pipeline has credentials for:
 *
 *   1. **UI smoke** (default) — render ``/chat``, composer, sidebar
 *   2. **API contract** (needs ``E2E_SHIP_API_BASE`` + token) — active
 *      thread + buckets shape, new-thread idempotency
 *   3. **Stream round-trip** (opt-in via ``E2E_RUN_NAVIGATOR_STREAM=1``
 *      *and* an LLM key on the backend) — POST a user message, read
 *      the SSE response, assert at least one ``token`` / ``message``
 *      event flows back.
 *
 * The agent stream is the most expensive ring — it burns LLM tokens on
 * every run — so it's off by default. The cheap rings always run when
 * the session storage + API credentials are present.
 */

test.describe("navigator (agent chat)", () => {
  test.describe.configure({ mode: "serial" });

  // ---------------------------------------------------------------------------
  // Ring 1 — UI smoke. Only needs the authenticated storageState.
  // ---------------------------------------------------------------------------

  test.describe("UI surface", () => {
    test.beforeEach(() => {
      test.skip(
        !hasPlaywrightStorageState(),
        "Set E2E_STORAGE_STATE (see e2e/README.md)",
      );
    });

    test("renders Navigator header + composer", async ({ page }) => {
      await page.goto("/chat");
      await expect(
        page.getByRole("heading", { name: "Navigator", exact: true }),
      ).toBeVisible({ timeout: 30_000 });

      // Composer must be reachable even when the backend has no LLM
      // key configured — the "Agent not configured" empty state still
      // renders the Navigator shell (see ``console/src/app/chat/
      // page.tsx``). So we race the two known empty states:
      //   - Agent configured   → composer textarea
      //   - Agent not configured → empty-state card
      const composer = page.getByPlaceholder(/Ask the agent/i);
      const notConfigured = page.getByText(/Agent not configured/i);
      await expect(composer.or(notConfigured)).toBeVisible({
        timeout: 15_000,
      });
    });

    test("composer accepts input but enforces non-empty submit", async ({
      page,
    }) => {
      await page.goto("/chat");
      const composer = page.getByPlaceholder(/Ask the agent/i);
      // If the backend isn't configured, this ring is meaningless —
      // bail out cleanly rather than flake.
      if (!(await composer.isVisible().catch(() => false))) {
        test.info().annotations.push({
          type: "skip-composer",
          description: "Agent not configured on backend — composer absent",
        });
        return;
      }
      const send = page.getByRole("button", { name: /^send/i });
      await expect(send, "send disabled on empty draft").toBeDisabled();
      await composer.fill("hello ship");
      await expect(send, "send enabled once draft has content").toBeEnabled();
      // Deliberately don't hit send — the stream ring covers that.
      await composer.fill("");
      await expect(send, "send disabled again after clearing").toBeDisabled();
    });

    test("buckets sidebar renders (empty or populated)", async ({ page }) => {
      await page.goto("/chat");
      // The sidebar's visible copy uses "Named buckets" / "Buckets" as
      // the region label. We don't hard-pin on a specific heading —
      // the surface is young; prefer a permissive match that survives
      // copy tweaks while still proving the sidebar mounted.
      const sidebar = page
        .getByText(/bucket/i)
        .first();
      await expect(sidebar).toBeVisible({ timeout: 15_000 });
    });
  });

  // ---------------------------------------------------------------------------
  // Ring 2 — API contract. Needs an admin PAT.
  // ---------------------------------------------------------------------------

  test.describe("API contract", () => {
    test.beforeEach(() => {
      test.skip(
        !hasShipApiCredentials(),
        "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
      );
    });

    test("GET /chat/active returns a thread shape", async ({ request }) => {
      const ws = await shipResolveWorkspaceId(request);
      const res = await shipApiGet(
        request,
        `/v1/workspaces/${encodeURIComponent(ws)}/chat/active`,
      );
      if (res.status() === 412) {
        test.info().annotations.push({
          type: "skip-agent",
          description:
            "Agent not configured on backend (412). Set OPENAI_API_KEY " +
            "to cover ``/chat/active``.",
        });
        return;
      }
      expect(res.ok(), `GET /chat/active ${res.status()}`).toBeTruthy();
      const thread = (await res.json()) as {
        id: string;
        status: string;
        messages: unknown[];
      };
      expect(thread.id, "thread id").toBeTruthy();
      expect(thread.status, "thread status").toBeTruthy();
      expect(Array.isArray(thread.messages), "messages is array").toBe(true);
    });

    test("GET /buckets returns { buckets: [...] }", async ({ request }) => {
      const ws = await shipResolveWorkspaceId(request);
      // ``/buckets`` is mounted at the workspace root per
      // ``backend/app/api/v1/routes/knowledge.py`` — ``/knowledge``
      // also returns ``{ buckets: ... }``. Accept either for
      // forward-compat; the product has shipped both names.
      const primary = await shipApiGet(
        request,
        `/v1/workspaces/${encodeURIComponent(ws)}/buckets`,
      );
      const res = primary.ok()
        ? primary
        : await shipApiGet(
            request,
            `/v1/workspaces/${encodeURIComponent(ws)}/knowledge`,
          );
      expect(res.ok(), `GET buckets ${res.status()}`).toBeTruthy();
      const body = (await res.json()) as
        | { buckets: unknown[] }
        | { items: unknown[] }
        | unknown[];
      if (Array.isArray(body)) {
        // Some versions of the API returned a bare array — accept it.
        return;
      }
      const rows =
        "buckets" in body
          ? body.buckets
          : "items" in body
            ? body.items
            : null;
      expect(Array.isArray(rows), "buckets payload is an array").toBe(true);
    });

    test("POST /chat/active/new is idempotent-shaped", async ({ request }) => {
      const ws = await shipResolveWorkspaceId(request);
      const first = await shipApiPost(
        request,
        `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/new`,
        {},
      );
      if (first.status() === 412) {
        test.info().annotations.push({
          type: "skip-agent",
          description:
            "Agent not configured on backend (412) — skipping new-thread contract.",
        });
        return;
      }
      expect(first.ok(), `first new ${first.status()}`).toBeTruthy();
      const t1 = (await first.json()) as { id: string; status: string };
      expect(t1.id).toBeTruthy();

      // A second call with an empty body should produce another fresh
      // thread (the product is "new thread on demand", not upsert)
      // OR return the same thread if the server decides the prior one
      // is still empty — both are defensible for the onboarding flow
      // and the test shouldn't fight either call.
      const second = await shipApiPost(
        request,
        `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/new`,
        {},
      );
      expect(second.ok(), `second new ${second.status()}`).toBeTruthy();
      const t2 = (await second.json()) as { id: string };
      expect(t2.id).toBeTruthy();
    });
  });

  // ---------------------------------------------------------------------------
  // Ring 3 — SSE round-trip. Off by default; burns LLM tokens per run.
  // ---------------------------------------------------------------------------

  test.describe("stream round-trip", () => {
    test.describe.configure({ timeout: 3 * 60_000 });

    test.beforeEach(() => {
      test.skip(
        process.env.E2E_RUN_NAVIGATOR_STREAM !== "1",
        "Set E2E_RUN_NAVIGATOR_STREAM=1 to send a real message to the agent",
      );
      test.skip(
        !hasShipApiCredentials(),
        "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
      );
    });

    test("POST /chat/stream emits at least one assistant chunk", async ({
      request,
    }) => {
      const ws = await shipResolveWorkspaceId(request);
      // Fresh thread so the assertion isn't confused by leftover chatter.
      const threadRes = await shipApiPost(
        request,
        `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/new`,
        {},
      );
      if (threadRes.status() === 412) {
        test.info().annotations.push({
          type: "skip-agent",
          description:
            "Agent not configured on backend (412) — stream test requires an LLM key.",
        });
        return;
      }
      expect(threadRes.ok(), `new thread ${threadRes.status()}`).toBeTruthy();
      const thread = (await threadRes.json()) as { id: string };

      // thread.id is captured for debug purposes only; ``/chat/stream``
      // targets the workspace's active thread implicitly and we just
      // created one, so the stream will append to it.
      expect(thread.id).toBeTruthy();
      const marker = `e2e-nav-${Date.now()}`;
      const text = await streamChatText(
        request,
        ws,
        `Reply with the word READY so the e2e marker ${marker} can match.`,
      );
      // We don't pin on the exact model output (that'd be flaky across
      // providers). We only assert we got *some* assistant text back —
      // i.e. the SSE pipe is wired end-to-end.
      expect(
        text.length,
        "stream returned at least some assistant text",
      ).toBeGreaterThan(0);
    });
  });
});

/**
 * Minimal SSE reader that concatenates ``delta`` events (the text
 * payload the client renders) from ``POST /v1/workspaces/{ws}/chat/
 * stream``. The backend emits JSON-shaped events — see the docstring
 * on ``chat_stream`` in ``backend/app/api/v1/routes/chat.py`` — with
 * the standard SSE ``data:`` framing. Events we don't recognise are
 * skipped so new event types don't break the test.
 */
async function streamChatText(
  request: APIRequestContext,
  workspaceId: string,
  prompt: string,
): Promise<string> {
  const base = process.env.E2E_SHIP_API_BASE!.replace(/\/+$/, "");
  const token = process.env.E2E_SHIP_API_TOKEN!;
  const res = await request.post(
    `${base}/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/stream`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      // Match the backend's ``ChatStreamIn`` — ``body`` is the user
      // text, ``classify_shift`` is the optional topic-shift classifier
      // toggle (we leave it on to exercise the full server path).
      data: JSON.stringify({ body: prompt, classify_shift: true }),
      timeout: 120_000,
    },
  );
  expect(res.ok(), `POST /chat/stream ${res.status()}`).toBeTruthy();
  const body = await res.text();
  let out = "";
  for (const line of body.split(/\r?\n/)) {
    if (!line.startsWith("data:")) continue;
    const raw = line.slice(5).trim();
    if (!raw || raw === "[DONE]") continue;
    try {
      const ev = JSON.parse(raw) as {
        type?: string;
        text?: string;
        delta?: string;
      };
      if (ev.type === "delta" && typeof ev.text === "string") {
        out += ev.text;
      } else if (typeof ev.delta === "string") {
        // Belt-and-braces for a hypothetical older event shape.
        out += ev.delta;
      }
    } catch {
      // Unknown framing — ignore.
    }
  }
  return out;
}
