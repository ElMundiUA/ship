import { expect, test } from "@playwright/test";

/**
 * Прямой контракт Ship **HTTP API** `/v1/*` (оркестрация).
 * Браузер не нужен — достаточно Bearer-токена пользователя/CLI.
 *
 * Env:
 *   E2E_SHIP_API_BASE   — origin бэкенда без хвоста `/`, напр. `https://api.your-ship.example.com`
 *                         (тот же URL, что в `SHIP_API_URL` у консоли).
 *   E2E_SHIP_API_TOKEN  — Bearer (mint: Console → Workspace settings / CLI tokens).
 *   E2E_WORKSPACE_ID    — optional UUID; иначе берётся `GET /v1/workspaces`[0].
 */

function apiBase(): string | null {
  const b = process.env.E2E_SHIP_API_BASE?.trim().replace(/\/+$/, "");
  return b && b.length > 0 ? b : null;
}

function bearer(): string | null {
  const t = process.env.E2E_SHIP_API_TOKEN?.trim();
  return t && t.length > 0 ? t : null;
}

async function shipGet(
  request: import("@playwright/test").APIRequestContext,
  path: string,
) {
  const base = apiBase()!;
  return request.get(`${base}${path}`, {
    headers: {
      Authorization: `Bearer ${bearer()}`,
      Accept: "application/json",
    },
  });
}

test.describe("Ship API — /v1 workspace orchestration (sandbox)", () => {
  test.beforeEach(() => {
    test.skip(!apiBase() || !bearer(), "Set E2E_SHIP_API_BASE and E2E_SHIP_API_TOKEN");
  });

  test("GET /v1/workspaces returns at least one workspace", async ({
    request,
  }) => {
    const res = await shipGet(request, "/v1/workspaces");
    expect(res.ok(), `GET /v1/workspaces → ${res.status()}`).toBeTruthy();
    const data = (await res.json()) as unknown[];
    expect(Array.isArray(data)).toBeTruthy();
    expect(data.length).toBeGreaterThan(0);
  });

  test("GET pipelines, dashboard, clarifications, improvements", async ({
    request,
  }) => {
    let wsId = process.env.E2E_WORKSPACE_ID?.trim();
    if (!wsId) {
      const r = await shipGet(request, "/v1/workspaces");
      expect(r.ok()).toBeTruthy();
      const list = (await r.json()) as { id: string }[];
      wsId = list[0]?.id;
    }
    expect(wsId, "workspace id").toBeTruthy();

    const enc = encodeURIComponent(wsId!);

    const pip = await shipGet(request, `/v1/workspaces/${enc}/pipelines`);
    expect(pip.ok(), `pipelines ${pip.status()}`).toBeTruthy();

    const dash = await shipGet(request, `/v1/workspaces/${enc}/dashboard`);
    expect(dash.ok(), `dashboard ${dash.status()}`).toBeTruthy();
    const board = (await dash.json()) as {
      counts: unknown;
      pipeline_runs: unknown[];
      workflow_runs: unknown[];
    };
    expect(board.counts).toBeTruthy();
    expect(Array.isArray(board.pipeline_runs)).toBeTruthy();
    expect(Array.isArray(board.workflow_runs)).toBeTruthy();

    const clar = await shipGet(request, `/v1/workspaces/${enc}/clarifications`);
    expect(clar.ok(), `clarifications ${clar.status()}`).toBeTruthy();
    expect(Array.isArray(await clar.json())).toBeTruthy();

    const impr = await shipGet(request, `/v1/workspaces/${enc}/improvements`);
    expect(impr.ok(), `improvements ${impr.status()}`).toBeTruthy();
    expect(Array.isArray(await impr.json())).toBeTruthy();
  });
});
