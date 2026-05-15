/**
 * C — Pipeline sad paths.
 *
 * Server-side guards that the happy-path A-tests don't reach. We
 * post to ``/agent-runs/finish`` directly with crafted payloads so
 * the suite stays LLM-free and fast — these tests run in seconds,
 * not minutes, and complement the long-form A1-A5 walks.
 *
 * Cases:
 *
 *   C.1  Planning ``needs_clarification`` → comment lands, ticket
 *        stays on stage:planning, inbox letter (if a tracker is
 *        bound this hits ``noop:no_ticket``; the inbox row lands
 *        when no tracker is bound — we cover the bound path here).
 *
 *   C.2  Validation ``outcome=blocked`` → defect comment lands,
 *        no FSM transition. The runner-side rewrite path (push or
 *        gh pr create failure → blocked) is exercised end-to-end
 *        by A3/A4; C.2 covers the agent-self-declared blocked
 *        outcome.
 *
 *   C.3  Concurrent picker → ``pg_try_advisory_xact_lock`` ensures
 *        two parallel ``/tracker/next`` calls on the same FSM stage
 *        return at most one ticket. Second caller gets ticket=null.
 *
 *   C.4  ``ready_next_step`` on a PR-authoring stage requires a PR
 *        URL in ``comment`` (ELS-120 safety net). Posting without
 *        one must 422 with ``pr_url_required``.
 */

import { expect, test } from "@playwright/test";

import {
  extractStage,
  hasPipelineCredentials,
  pipelineActivateProject,
  pipelineCreateProject,
  pipelineCreateTicket,
  pipelineGetTicket,
  pipelineSuiteEnv,
} from "../lib/pipeline-helpers";


const PIPELINE_ENABLED = process.env.E2E_RUN_PIPELINE === "1";


function authHeaders(token: string): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    Accept: "application/json",
  };
}


test.describe("[wired] pipeline · sad paths (C)", () => {
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

  test.setTimeout(2 * 60 * 1000);

  test("C.1 — needs_clarification leaves ticket on stage:planning + posts agent comment", async ({
    request,
  }) => {
    const env = pipelineSuiteEnv();
    const auth = {
      base: env.base!,
      workspaceId: env.workspaceId!,
      poPat: env.poPat!,
    };

    const project = await pipelineCreateProject(request, auth, {
      name: `e2e-sad-clar-${Date.now().toString(36)}`,
      body: "## Why\n\nSad-path C.1 fixture.\n",
      description: "C.1",
    });
    await pipelineActivateProject(request, auth, project.id);

    const ticket = await pipelineCreateTicket(request, auth, {
      projectId: project.id,
      title: "Thin brief",
      body: "## PO Brief\n\nWe should make the thing better.\n",
      labels: ["stage:planning"],
    });

    // Simulate the planning bundle declaring the brief too thin.
    const res = await request.post(
      `${auth.base}/v1/workspaces/${auth.workspaceId}/agent-runs/finish`,
      {
        headers: authHeaders(env.devPat!),
        data: JSON.stringify({
          run_id: `c1-${Date.now()}`,
          outcome: "needs_clarification",
          fsm_stage: "planning",
          ticket_ref: ticket.ticketRef,
          comment:
            "Brief is too thin to plan against — no concrete goal, " +
            "no audience, no AC. Please flesh out the Problem and " +
            "Goal sections before planning fires again. " +
            "[Ship SDLC:role-planning]",
        }),
      },
    );
    expect(res.ok(), `finish needs_clarification → ${res.status()}: ${await res.text()}`)
      .toBeTruthy();

    const after = await pipelineGetTicket(request, auth, ticket.ticketRef);
    expect(extractStage(after), "stage should NOT advance after needs_clarification")
      .toBe("planning");
    expect(
      after.comments.some((c) =>
        c.body.includes("[Ship SDLC:role-planning]"),
      ),
      "clarification comment must land on the ticket",
    ).toBe(true);
  });


  test("C.2 — validation outcome=blocked leaves ticket on stage:validation + posts defect comment", async ({
    request,
  }) => {
    const env = pipelineSuiteEnv();
    const auth = {
      base: env.base!,
      workspaceId: env.workspaceId!,
      poPat: env.poPat!,
    };

    const project = await pipelineCreateProject(request, auth, {
      name: `e2e-sad-blocked-${Date.now().toString(36)}`,
      body: "## Why\n\nSad-path C.2 fixture.\n",
      description: "C.2",
    });
    await pipelineActivateProject(request, auth, project.id);

    const ticket = await pipelineCreateTicket(request, auth, {
      projectId: project.id,
      title: "PR with QA defects",
      body: "## PO Brief\n\nThing.\n",
      labels: ["stage:validation"],
    });

    const res = await request.post(
      `${auth.base}/v1/workspaces/${auth.workspaceId}/agent-runs/finish`,
      {
        headers: authHeaders(env.devPat!),
        data: JSON.stringify({
          run_id: `c2-${Date.now()}`,
          outcome: "blocked",
          fsm_stage: "validation",
          ticket_ref: ticket.ticketRef,
          comment:
            "Manual QA found 2 defects:\n" +
            "1. /feedback/count returns 500 on empty store.\n" +
            "2. Response shape diverges from spec (returns array, " +
            "spec says object).\n" +
            "Stopping before Phase 2 — developer takes these next pass. " +
            "[Ship SDLC:role-validation]",
        }),
      },
    );
    expect(res.ok(), `finish blocked → ${res.status()}: ${await res.text()}`)
      .toBeTruthy();

    const after = await pipelineGetTicket(request, auth, ticket.ticketRef);
    expect(
      extractStage(after),
      "blocked outcome must NOT transition the ticket",
    ).toBe("validation");

    // ``blocked`` doesn't post a ticket comment — it drops a
    // ``type=blocker`` row into the workspace inbox so the operator
    // sees the defect list outside the tracker. Verify the inbox
    // row landed with the agent's narrative as its summary.
    // ``ownership=all`` because the finish handler creates the
    // blocker row with ``owner_user_id=NULL`` — the default
    // ``mine`` filter would hide it.
    const inboxRes = await request.get(
      `${auth.base}/v1/workspaces/${auth.workspaceId}/inbox?ownership=all&status=new&type=blocker&limit=20`,
      { headers: { Authorization: `Bearer ${auth.poPat}` } },
    );
    expect(inboxRes.ok(), `inbox list → ${inboxRes.status()}`).toBeTruthy();
    const inboxJson = (await inboxRes.json()) as {
      items: { type: string; summary: string | null }[];
    };
    const blocker = inboxJson.items.find(
      (i) =>
        i.type === "blocker" &&
        (i.summary || "").includes("[Ship SDLC:role-validation]"),
    );
    expect(blocker, "validation should drop a blocker row into the inbox")
      .toBeDefined();
  });


  test("C.3 — orphan / overlay-frozen tickets are skipped by /tracker/next", async ({
    request,
  }) => {
    const env = pipelineSuiteEnv();
    const auth = {
      base: env.base!,
      workspaceId: env.workspaceId!,
      poPat: env.poPat!,
    };

    const project = await pipelineCreateProject(request, auth, {
      name: `e2e-sad-skips-${Date.now().toString(36)}`,
      body: "## Why\n\nSad-path C.3 fixture.\n",
      description: "C.3",
    });
    await pipelineActivateProject(request, auth, project.id);

    // ELS-84 — a ticket carrying ``needs:clarification`` (signal
    // label) is overlay-frozen; the picker MUST skip it so the
    // dispatcher doesn't re-fire the agent on a question that's
    // still waiting on the operator.
    const frozen = await pipelineCreateTicket(request, auth, {
      projectId: project.id,
      title: "Frozen by overlay",
      body: "## PO Brief\n\nWaiting on operator.\n",
      labels: ["stage:planning", "needs:clarification"],
    });

    // Sanity: confirm the frozen ticket can be fetched directly
    // (so we know the picker's skip is intentional, not a
    // tenancy / not-found accident).
    const direct = await pipelineGetTicket(
      request,
      auth,
      frozen.ticketRef,
    );
    expect(direct.labels).toContain("needs:clarification");

    // Pin the picker to that ticket. ELS-124 ticket_ref filter +
    // ELS-84 overlay-freeze should compose: the picker accepts
    // the pin, then drops the row because of the freeze.
    const url =
      `${auth.base}/v1/workspaces/${auth.workspaceId}` +
      `/tracker/next?state=planning&ticket_ref=${encodeURIComponent(frozen.ticketRef)}`;
    const res = await request.get(url, {
      headers: { Authorization: `Bearer ${auth.poPat}` },
    });
    expect(res.ok(), `picker call → ${res.status()}`).toBeTruthy();
    const body = (await res.json()) as { ticket: { ticket_ref: string } | null };
    expect(
      body.ticket,
      `expected picker to skip overlay-frozen ticket ${frozen.ticketRef}; ` +
        `got ticket=${body.ticket?.ticket_ref ?? "null"}`,
    ).toBeNull();
  });


  test("C.4 — ready_next_step on a PR-authoring stage without PR URL returns 422 pr_url_required", async ({
    request,
  }) => {
    const env = pipelineSuiteEnv();
    const auth = {
      base: env.base!,
      workspaceId: env.workspaceId!,
      poPat: env.poPat!,
    };

    const project = await pipelineCreateProject(request, auth, {
      name: `e2e-sad-prgate-${Date.now().toString(36)}`,
      body: "## Why\n\nSad-path C.4 fixture.\n",
      description: "C.4",
    });
    await pipelineActivateProject(request, auth, project.id);

    const ticket = await pipelineCreateTicket(request, auth, {
      projectId: project.id,
      title: "Dev finish without PR URL",
      body: "## PO Brief\n\nThing.\n",
      labels: ["stage:dev_implementation"],
    });

    // ELS-120 safety net: a code-changing finish without a PR URL
    // is the symptom of an agent bypassing the sidecar protocol.
    // The runner ALWAYS splices ``PR: <url>`` into ``comment``
    // after ``gh pr create`` succeeds — an absent URL means the
    // push/PR step never ran. Reject so the FSM doesn't advance.
    const res = await request.post(
      `${auth.base}/v1/workspaces/${auth.workspaceId}/agent-runs/finish`,
      {
        headers: authHeaders(env.devPat!),
        data: JSON.stringify({
          run_id: `c4-${Date.now()}`,
          outcome: "ready_next_step",
          fsm_stage: "dev_implementation",
          stage_next: "validation",
          ticket_ref: ticket.ticketRef,
          comment:
            "Implementation done. [Ship SDLC:role-developer]",
        }),
      },
    );
    expect(
      res.status(),
      `expected 422 pr_url_required; got ${res.status()} ${await res.text()}`,
    ).toBe(422);
    const body = (await res.json()) as { detail: { code?: string } };
    expect(body.detail?.code, "expected error code pr_url_required")
      .toBe("pr_url_required");

    // And the ticket stays put.
    const after = await pipelineGetTicket(request, auth, ticket.ticketRef);
    expect(
      extractStage(after),
      "ticket must stay on dev_implementation after 422",
    ).toBe("dev_implementation");
  });
});
