/**
 * A0 — Navigator-driven planning anchor seed (E17/ELS-129 + E20).
 *
 * Asserts the chat-to-planning path: the PO opens a "shape a project"
 * thread, describes a feature in prose, and the Navigator's
 * ``project_create`` tool fires to land:
 *
 *   1. A new project on the workspace's bound tracker.
 *   2. A ``planning:anchor`` issue under that project (idempotent
 *      seed via ``_ensure_planning_anchor``).
 *   3. A ``WorkspaceProjectPriority`` row in the ``planning``
 *      bucket (drafts) — visible on the operator dashboard without
 *      gating the dispatcher's ELS-80 picker.
 *
 * The decomposition pipeline picks up from here in A2: a fresh
 * ``stage:decomposition`` label is what tells the bundle "this anchor
 * is ready". A0 stops at the anchor existing — adding the stage
 * label is the operator's hand-off (or a follow-up Navigator turn).
 *
 * Cost: one Navigator chat round; ≈ 30-90s.
 */

import { expect, test } from "@playwright/test";

import {
  analyseToolTrajectory,
  streamNavigatorTurn,
} from "../lib/navigator-sse";
import {
  hasPipelineCredentials,
  pipelineGetProjectPriority,
  pipelineSuiteEnv,
} from "../lib/pipeline-helpers";


const PIPELINE_ENABLED = process.env.E2E_RUN_PIPELINE === "1";


test.describe("[wired] pipeline · navigator drafts a project (A0)", () => {
  test.beforeAll(() => {
    if (!PIPELINE_ENABLED) {
      test.skip(true, "set E2E_RUN_PIPELINE=1 to run pipeline tests");
    }
    if (!hasPipelineCredentials()) {
      test.skip(
        true,
        "pipeline credentials missing — run seed_e2e_pipeline_workspace.py first",
      );
    }
  });

  test.setTimeout(5 * 60 * 1000);

  test("Navigator project_create seeds project + planning anchor + drafts priorities row", async ({
    request,
  }, testInfo) => {
    const env = pipelineSuiteEnv();
    const base = env.base!;
    const token = env.poPat!;
    const workspaceId = env.workspaceId!;

    // ---- arrange: open a fresh shape-project thread -----------------------
    // POST /chat/active/new with intent=shape_project archives the
    // active thread and opens a planning-biased one. Without this
    // the Navigator's intent is generic and the agent won't call
    // project_create on a "make me a project" message.

    const threadRes = await request.post(
      `${base}/v1/workspaces/${workspaceId}/chat/active/new`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        data: JSON.stringify({ intent: "shape_project" }),
      },
    );
    expect(threadRes.ok(), `open shape_project thread failed: ${await threadRes.text()}`)
      .toBeTruthy();

    // ---- act: chat the brief in plain prose --------------------------------

    const projectName = `e2e-nav-${Date.now().toString(36)}`;
    const prompt =
      `Шейпим проект "${projectName}". ` +
      `Хочу добавить фичу bulk-retention для feedback API: оператор ` +
      `задаёт N дней, сервис удаляет в фоне все feedback-строки ` +
      `старше N дней, а дашборд показывает счётчик "будет удалено в ` +
      `следующий проход". Бери название проекта как есть и заведи ` +
      `его через инструмент project_create — у меня есть PO Brief в ` +
      `описании, и нужен planning anchor для декомпозиции.`;

    const stream = await streamNavigatorTurn(request, {
      base,
      token,
      workspaceId,
      body: prompt,
      classifyShift: false,
      // Don't blow away the fresh thread we just opened.
      freshThread: false,
      timeoutMs: 240_000,
    });

    expect(
      stream.status,
      `chat stream returned ${stream.status}: ${stream.text.slice(0, 200)}`,
    ).toBe(200);

    // ---- assert: project_create tool fired and succeeded -------------------

    const analysis = analyseToolTrajectory(stream.events);
    testInfo.annotations.push({
      type: "tools-fired",
      description:
        analysis.invocations.map((i) => `${i.name}${i.ok ? "✓" : "✗"}`).join(
          ", ",
        ) || "(none)",
    });

    const projectCreate = analysis.invocations.find(
      (i) => i.name === "project_create",
    );
    expect(
      projectCreate,
      `expected project_create tool call; got: ${analysis.invocations
        .map((i) => i.name)
        .join(", ")}`,
    ).toBeDefined();
    expect(
      projectCreate!.ok,
      `project_create errored: ${projectCreate!.error}`,
    ).toBe(true);

    // Pull the resulting project id out of the tool result for the
    // priorities-row assertion below. Tool results land as JSON-
    // serialised strings under ``result`` — the shape is
    // ``{ id, name, slug, anchor: { id, identifier, url }, … }``.
    const rawResult = projectCreate!.result;
    let projectId = "";
    let anchorIdentifier = "";
    if (typeof rawResult === "string") {
      try {
        const parsed = JSON.parse(rawResult) as {
          id?: string;
          anchor?: { identifier?: string };
        };
        projectId = parsed.id ?? "";
        anchorIdentifier = parsed.anchor?.identifier ?? "";
      } catch {
        /* leave empty; assertion below will surface */
      }
    } else if (rawResult && typeof rawResult === "object") {
      const obj = rawResult as {
        id?: string;
        anchor?: { identifier?: string };
      };
      projectId = obj.id ?? "";
      anchorIdentifier = obj.anchor?.identifier ?? "";
    }
    expect(projectId, "project_create result must carry a project id")
      .toMatch(/^[0-9a-f-]{36}$/);
    testInfo.annotations.push({ type: "project-id", description: projectId });
    testInfo.annotations.push({
      type: "anchor",
      description: anchorIdentifier || "(no anchor returned)",
    });

    // ---- assert: anchor created with planning:anchor label -----------------

    expect(
      anchorIdentifier,
      "project_create should return an anchor identifier for trackers that model anchors (memory / Linear)",
    ).toMatch(/^MEM-\d+|^[A-Z]+-\d+$/);

    const anchorRes = await request.get(
      `${base}/v1/workspaces/${workspaceId}/local-tracker/tickets/${anchorIdentifier}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    expect(anchorRes.ok(), `anchor lookup failed: ${anchorRes.status()}`)
      .toBeTruthy();
    const anchor = (await anchorRes.json()) as { labels: string[] };
    expect(
      anchor.labels,
      "anchor must carry planning:anchor label",
    ).toContain("planning:anchor");

    // ---- assert: priorities row landed in the drafts bucket ----------------

    const priorityBucket = await pipelineGetProjectPriority(
      request,
      { base, workspaceId, poPat: token },
      projectId,
    );
    expect(
      priorityBucket,
      `expected priorities row in 'planning' bucket (drafts); got ${priorityBucket}`,
    ).toBe("planning");
  });
});
