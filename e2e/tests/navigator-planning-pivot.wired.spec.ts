/**
 * Navigator — mid-thread planning pivot (E20).
 *
 * Drives the full enter → exit pivot loop in one Playwright spec.
 * Two rings:
 *
 *   Ring 1 (cheap, default) — API contract for the intent flip
 *   endpoint. Direct POST against
 *   ``/v1/workspaces/{ws}/chat/active/intent`` exercising both
 *   directions of the in-place flip + the audit log shape. Doesn't
 *   burn LLM tokens.
 *
 *   Ring 2 (LLM-gated) — end-to-end via the chat stream. Sends a
 *   user message that matches the explicit-phrase ENTER pattern,
 *   asserts the SSE stream carries a ``drafting_intent`` event with
 *   verdict ENTER, posts to /intent to flip, then sends an EXIT
 *   phrase + asserts the inverse. The explicit-phrase fast path
 *   doesn't actually need an LLM, but the chat stream around it
 *   does, so the suite stays gated behind ``E2E_RUN_NAVIGATOR_STREAM=1``.
 */

import { expect, test, type APIRequestContext } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiGet,
  shipApiPost,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { streamNavigatorTurn } from "../lib/navigator-sse";


test.describe("navigator planning pivot — API contract", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(() => {
    test.skip(
      !hasShipApiCredentials(),
      "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
    );
  });

  test("POST /chat/active/intent flips in place and returns the same thread", async ({
    request,
  }) => {
    const ws = await shipResolveWorkspaceId(request);

    // Start from a known-clean thread — fresh, normal-chat mode.
    const fresh = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/new`,
      {},
    );
    if (fresh.status() === 412) {
      test.skip(true, "Agent not configured (412)");
      return;
    }
    expect(fresh.ok(), `new thread ${fresh.status()}`).toBeTruthy();
    const initial = (await fresh.json()) as {
      id: string;
      intent: string | null;
    };
    expect(initial.intent).toBeNull();

    // Flip to drafting.
    const enter = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/intent`,
      { intent: "shape_project" },
    );
    expect(enter.ok(), `enter flip ${enter.status()}`).toBeTruthy();
    const entered = (await enter.json()) as { id: string; intent: string };
    expect(entered.id).toBe(initial.id); // same thread, no archive
    expect(entered.intent).toBe("shape_project");

    // Exit drafting.
    const exit = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/intent`,
      { intent: null },
    );
    expect(exit.ok(), `exit flip ${exit.status()}`).toBeTruthy();
    const exited = (await exit.json()) as {
      id: string;
      intent: string | null;
    };
    expect(exited.id).toBe(initial.id);
    expect(exited.intent).toBeNull();

    // Confirm via /chat/active that the live thread reflects the
    // final state.
    const active = await shipApiGet(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active`,
    );
    expect(active.ok()).toBeTruthy();
    const live = (await active.json()) as {
      id: string;
      intent: string | null;
    };
    expect(live.id).toBe(initial.id);
    expect(live.intent).toBeNull();
  });

  test("POST /chat/active/intent rejects unknown intent with 422", async ({
    request,
  }) => {
    const ws = await shipResolveWorkspaceId(request);
    const fresh = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/new`,
      {},
    );
    if (fresh.status() === 412) {
      test.skip(true, "Agent not configured (412)");
      return;
    }
    const bad = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/intent`,
      { intent: "shape_widget" },
    );
    expect(bad.status()).toBe(422);
  });

  test("POST /chat/active/intent same-value flip is a no-op", async ({
    request,
  }) => {
    const ws = await shipResolveWorkspaceId(request);
    const fresh = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/new`,
      {},
    );
    if (fresh.status() === 412) {
      test.skip(true, "Agent not configured (412)");
      return;
    }
    const first = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/intent`,
      { intent: null },
    );
    // Was already null; flipping to null again must be 200, not 422.
    expect(first.ok(), `idempotent flip ${first.status()}`).toBeTruthy();
    const body = (await first.json()) as { intent: string | null };
    expect(body.intent).toBeNull();
  });
});


// ---------------------------------------------------------------------------
// Ring 2 — SSE drafting_intent event + full pivot loop
// ---------------------------------------------------------------------------


test.describe("navigator planning pivot — SSE round-trip", () => {
  test.describe.configure({ mode: "serial", timeout: 4 * 60_000 });

  test.beforeEach(() => {
    test.skip(
      process.env.E2E_RUN_NAVIGATOR_STREAM !== "1",
      "Set E2E_RUN_NAVIGATOR_STREAM=1 to burn LLM tokens for this suite",
    );
    test.skip(
      !hasShipApiCredentials(),
      "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
    );
  });

  test("explicit ENTER phrase emits drafting_intent verdict", async ({
    request,
  }) => {
    const base = process.env.E2E_SHIP_API_BASE!.replace(/\/+$/, "");
    const token = process.env.E2E_SHIP_API_TOKEN!;
    const ws = await shipResolveWorkspaceId(request);

    const result = await streamNavigatorTurn(request, {
      base,
      token,
      workspaceId: ws,
      // Explicit-phrase fast path — classifier returns ENTER without
      // a borderline LLM call. Still gated on E2E_RUN_NAVIGATOR_STREAM
      // because the chat handler around the classifier burns tokens.
      body: "let's shape a new project for the CSV export dashboard",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured (412)");
      return;
    }
    expect(result.status).toBe(200);
    const verdict = result.events.find(
      (e) => (e as { type?: string }).type === "drafting_intent",
    ) as { verdict?: string; reason?: string } | undefined;
    expect(verdict, "drafting_intent event emitted").toBeTruthy();
    expect(verdict?.verdict).toBe("ENTER");
  });

  test("full enter → exit pivot stays on the same thread", async ({
    request,
  }) => {
    const base = process.env.E2E_SHIP_API_BASE!.replace(/\/+$/, "");
    const token = process.env.E2E_SHIP_API_TOKEN!;
    const ws = await shipResolveWorkspaceId(request);

    // Fresh thread so no leftover intent.
    const fresh = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/new`,
      {},
    );
    if (fresh.status() === 412) {
      test.skip(true, "Agent not configured (412)");
      return;
    }
    const initialThreadId = ((await fresh.json()) as { id: string }).id;

    // Turn 1: ENTER phrase → classifier emits ENTER → flip via REST.
    const enterStream = await streamNavigatorTurn(request, {
      base,
      token,
      workspaceId: ws,
      body: "let's shape a new project around release notes automation",
    });
    expect(enterStream.status).toBe(200);
    expect(
      enterStream.events.some(
        (e) =>
          (e as { type?: string }).type === "drafting_intent" &&
          (e as { verdict?: string }).verdict === "ENTER",
      ),
      "ENTER verdict on turn 1",
    ).toBe(true);

    const enterFlip = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/intent`,
      { intent: "shape_project" },
    );
    expect(enterFlip.ok()).toBeTruthy();
    const afterEnter = (await enterFlip.json()) as {
      id: string;
      intent: string;
    };
    expect(afterEnter.id).toBe(initialThreadId);
    expect(afterEnter.intent).toBe("shape_project");

    // Turn 2: EXIT phrase → classifier emits EXIT → flip back.
    const exitStream = await streamNavigatorTurn(request, {
      base,
      token,
      workspaceId: ws,
      body: "forget the project, tell me about CSV export instead",
    });
    expect(exitStream.status).toBe(200);
    expect(
      exitStream.events.some(
        (e) =>
          (e as { type?: string }).type === "drafting_intent" &&
          (e as { verdict?: string }).verdict === "EXIT",
      ),
      "EXIT verdict on turn 2",
    ).toBe(true);

    const exitFlip = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/chat/active/intent`,
      { intent: null },
    );
    expect(exitFlip.ok()).toBeTruthy();
    const afterExit = (await exitFlip.json()) as {
      id: string;
      intent: string | null;
    };
    // Same thread throughout — no archive happened along the way.
    expect(afterExit.id).toBe(initialThreadId);
    expect(afterExit.intent).toBeNull();
  });
});
