import { expect, test } from "@playwright/test";

import {
  DEVELOPMENT_PROCESS_ID,
  fetchProcessEditorProbe,
  processEditorUrl,
  type ProcessEditorProbe,
} from "../lib/process-editor-helpers";
import { hasShipApiCredentials } from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Authenticated coverage for the process editor (`/process/*`).
 * Uses URL params (`?state=`, `?tab=schedule`, `?repo=`) and inspector
 * fields — no canvas drag/drop.
 *
 * Env: E2E_STORAGE_STATE (required), E2E_SHIP_API_* (probe), optional
 * E2E_SANDBOX_REPO, E2E_PROCESS_EDITOR_LOCKED_WORKSPACE_ID.
 */
test.describe("process editor (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  let probe: ProcessEditorProbe | null = null;

  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (see e2e/README.md)",
    );
  });

  test.beforeAll(async ({ request }) => {
    if (!hasShipApiCredentials()) return;
    probe = await fetchProcessEditorProbe(request);
  });

  function skipUnlessReady(): ProcessEditorProbe | null {
    if (!hasShipApiCredentials()) {
      test.info().annotations.push({
        type: "skip",
        description:
          "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN to probe workspace fixtures.",
      });
      return null;
    }
    if (!probe?.ready) {
      test.info().annotations.push({
        type: "skip",
        description:
          probe?.skipReason ??
          "Process editor fixtures unavailable for this workspace.",
      });
      return null;
    }
    return probe;
  }

  test("loads /process/development with process header and Flow/Capacity tabs", async ({
    page,
  }) => {
    const ctx = skipUnlessReady();
    if (!ctx) return;

    await page.goto(
      processEditorUrl({
        repoId: ctx.repoId!,
        workspaceId: ctx.workspaceId,
      }),
    );
    await expect(page).toHaveURL(
      new RegExp(`/process/${DEVELOPMENT_PROCESS_ID}`),
      { timeout: 30_000 },
    );
    await expect(
      page.getByRole("heading", { name: "Development", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByRole("navigation", { name: "Process view" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Flow", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Capacity", exact: true }),
    ).toBeVisible();
  });

  test("legacy ?tab=routines deep link opens Capacity tab", async ({ page }) => {
    const ctx = skipUnlessReady();
    if (!ctx) return;

    await page.goto(
      processEditorUrl({
        repoId: ctx.repoId!,
        tab: "routines",
        workspaceId: ctx.workspaceId,
      }),
    );
    await expect(
      page.getByRole("link", { name: "Capacity", exact: true }),
    ).toHaveAttribute("aria-current", "page");
    await expect(
      page.getByRole("heading", { name: "Capacity", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("Capacity tab (?tab=schedule) shows schedule UI and accepts edits", async ({
    page,
  }) => {
    const ctx = skipUnlessReady();
    if (!ctx) return;

    await page.goto(
      processEditorUrl({
        repoId: ctx.repoId!,
        tab: "schedule",
        workspaceId: ctx.workspaceId,
      }),
    );
    await expect(
      page.getByRole("link", { name: "Capacity", exact: true }),
    ).toHaveAttribute("aria-current", "page");
    await expect(
      page.getByRole("heading", { name: "Capacity", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Mon", { exact: true }).first()).toBeVisible();
    const addWindow = page.getByRole("button", { name: "+ Time window" });
    await expect(addWindow).toBeVisible();
    await addWindow.click();
    await expect(page.getByText("Review before publishing")).toBeVisible({
      timeout: 10_000,
    });
  });

  test("stage inspector edit surfaces validation and dirty UI", async ({
    page,
  }) => {
    const ctx = skipUnlessReady();
    if (!ctx || !ctx.firstStateId) return;

    await page.goto(
      processEditorUrl({
        repoId: ctx.repoId!,
        stateId: ctx.firstStateId,
        workspaceId: ctx.workspaceId,
      }),
    );
    const stageName = page.getByLabel("Stage name", { exact: true });
    await expect(stageName).toBeVisible({ timeout: 30_000 });
    const original = await stageName.inputValue();
    await stageName.fill("");
    await expect(page.getByText("Review before publishing")).toBeVisible({
      timeout: 10_000,
    });
    // Validation pill is shown when the draft has errors/warnings; clearing
    // the name alone may only dirty the draft (see ticket edge cases).
    const validationSummary = page.getByText(/\d+ (errors?|warnings?)/);
    if ((await validationSummary.count()) > 0) {
      await expect(validationSummary.first()).toBeVisible();
    }
    await stageName.fill(original);
  });

  test("repo selector updates ?repo= when multiple activated repos", async ({
    page,
  }) => {
    const ctx = skipUnlessReady();
    if (!ctx) return;

    if (ctx.repos.length < 2) {
      test.info().annotations.push({
        type: "skip",
        description:
          "Only one activated repo — RepoSelector is hidden by design.",
      });
      return;
    }

    await page.goto(
      processEditorUrl({
        repoId: ctx.repoId!,
        workspaceId: ctx.workspaceId,
      }),
    );
    const select = page.locator('label:has-text("Repo") select');
    await expect(select).toBeVisible({ timeout: 15_000 });
    const secondRepoId = ctx.repos.find((r) => r.id !== ctx.repoId)?.id;
    expect(secondRepoId).toBeTruthy();
    await select.selectOption(secondRepoId!);
    await expect(page).toHaveURL(
      new RegExp(`[?&]repo=${encodeURIComponent(secondRepoId!)}`),
      { timeout: 15_000 },
    );
    await expect(
      page.getByRole("heading", { name: "Development", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("locked workspace shows prerequisite banner and non-interactive editor", async ({
    page,
  }) => {
    const lockedWs = process.env.E2E_PROCESS_EDITOR_LOCKED_WORKSPACE_ID?.trim();
    if (!lockedWs) {
      test.info().annotations.push({
        type: "skip",
        description:
          "Set E2E_PROCESS_EDITOR_LOCKED_WORKSPACE_ID to a workspace missing tracker/orchestrator/default agent.",
      });
      return;
    }

    await page.goto(
      processEditorUrl({
        processId: DEVELOPMENT_PROCESS_ID,
        workspaceId: lockedWs,
      }),
    );
    await expect(
      page.getByRole("heading", {
        name: "Configure prerequisites before editing this process",
        exact: true,
      }),
    ).toBeVisible({ timeout: 30_000 });
    const lockedFieldset = page.locator("fieldset.pointer-events-none");
    await expect(lockedFieldset).toHaveCount(1);
    await expect(lockedFieldset).toHaveAttribute("aria-disabled", "true");
  });
});
