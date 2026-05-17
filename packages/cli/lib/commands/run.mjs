/**
 * `shipctl run` — E14 routine entry-point.
 *
 * Customer's GH Actions cron fires this once per routine slot. The
 * pipeline:
 *
 *   1. Read the routine from `.ship/config.yml` (specialist slug +
 *      optional inline prompt).
 *   2. Resolve the agent role body via the workspace endpoint
 *      `GET /v1/workspaces/{ws}/agent-roles/{slug}/resolve` —
 *      workspace overrides win, otherwise the Ship default. Pull
 *      the `system` (shared base) body in parallel.
 *   3. If the resolved role declares `fsm_stage`, ask Ship server
 *      for the next ticket in that stage
 *      (`GET /v1/.../tracker/next?state=<stage>`). Server picks the
 *      adapter (Linear / GH Issues / etc.) — CLI doesn't care.
 *   4. Mint a `run_id` and render the prompt: system body + role
 *      body + routine prompt + ticket details + a finish-protocol
 *      block with `SHIP_API_BASE`, `SHIP_API_TOKEN`, `SHIP_WORKSPACE_ID`,
 *      `RUN_ID`, `TICKET_REF`, `FSM_STAGE` already substituted so the
 *      agent can call the finish endpoint directly.
 *   5. Launch the configured agent runtime (`cli/lib/agents/`) — Cursor
 *      Cloud today. Block until the runtime terminates.
 *   6. The agent itself calls
 *      `POST /v1/workspaces/{ws}/agent-runs/finish` with its outcome.
 *      Ship's server applies tracker side-effects via the workspace
 *      Linear OAuth — the CLI doesn't read any branch / state file.
 *   7. CLI exits 0 on Cursor `FINISHED`, non-0 if the runtime crashed.
 *      Whether the agent actually called `/finish` is observable via
 *      the audit log; the smoke test for "did the right thing happen"
 *      is the tracker label/state itself.
 *
 * Env contract (typically wired in `ship-trigger-schedule.yml`):
 *   - SHIP_API_BASE         — Ship server, e.g. https://api.ship.elmundi.com
 *   - SHIP_API_TOKEN        — workspace API token (admin scope; rendered
 *                             into the agent's prompt so it can call
 *                             /agent-runs/finish from inside the agent)
 *   - SHIP_WORKSPACE_ID     — UUID of the workspace this run belongs to
 *   - CURSOR_API_KEY        — Cursor agent CLI auth (when provider=cursor)
 *   - ANTHROPIC_API_KEY     — Claude Code CLI auth (when provider=claude)
 *   - OPENAI_API_KEY        — OpenAI Codex CLI auth (when provider=codex)
 *   - GITHUB_REPOSITORY     — owner/repo (the repo the agent will check out)
 *
 * Note: there is **no SHIP_REPO_ID** — a workspace is the project, so
 * the tracker is workspace-scoped. ``GITHUB_REPOSITORY`` only tells the
 * agent runtime which checkout to spawn for code work. Branchless agents
 * (intake, BA, planner) don't push commits at all.
 */

import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { readConfig, findShipRoot } from "../config/io.mjs";
import { resolveExecutable } from "../runtime/routines.mjs";
import { DEFAULT_PROVIDER, runAgent } from "../agents/index.mjs";
import { fetchWithRetry } from "../retry.mjs";

const SYSTEM_ROLE_SLUG = "system";


const EXIT_OK = 0;
const EXIT_USAGE = 1;
const EXIT_NO_TASK = 0; // intentional: no eligible ticket is a clean noop
const EXIT_AGENT_FAIL = 7;


// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------


export async function runCommand(ctx, rest) {
  // Outer wrapper: any persistent edge 5xx after retries on the
  // preflight metadata calls (agent-role resolve, policies preamble,
  // tracker/next) noops cleanly with exit 0 instead of red-flagging
  // the whole cron tick. Same logic as ``shipctl trigger`` — the
  // ``*/30`` cron will retry from a (possibly different) runner with
  // a different network path. Real run errors (4xx, agent crash,
  // dispatch failures) still surface.
  try {
    return await _runCommandImpl(ctx, rest);
  } catch (err) {
    if (_isTransientHttpError(err)) {
      console.error(
        `warn: edge transient (${err instanceof Error ? err.message.split("\n")[0] : err}); skipping this run`,
      );
      console.log("Ship: nothing to do this run (edge unavailable).");
      process.exit(EXIT_OK);
    }
    throw err;
  }
}


function _isTransientHttpError(err) {
  if (!err) return false;
  const msg = err instanceof Error ? err.message : String(err);
  return (
    /\b50[234]\b/.test(msg)
    || /exhausted retries/.test(msg)
    || /agent-roles resolve 50[234]/.test(msg)
    || /tracker\/next 50[234]/.test(msg)
  );
}


async function _runCommandImpl(ctx, rest) {
  const args = parseArgs(rest);
  if (args.help) {
    printHelp();
    process.exit(EXIT_OK);
  }
  // ``--debug`` streams a single-line per-step log so an operator
  // running ``shipctl run`` from the workflow_dispatch UI can see
  // every phase of the run interleaved with the agent's own output.
  // Each step prints a stable prefix so the GHA log is greppable
  // (``# ship: [t+...] <stage> <status>``). Disabled by default —
  // production cron ticks stay terse.
  const stepStart = Date.now();
  const step = (stage, status, kv = {}) => {
    if (!args.debug) return;
    const t = ((Date.now() - stepStart) / 1000).toFixed(1);
    const pairs = Object.entries(kv)
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
      .join(" ");
    process.stderr.write(`# ship: [t+${t}s] ${stage} ${status}${pairs ? " " + pairs : ""}\n`);
  };
  if (!args.routine && !args.specialist) {
    die(
      EXIT_USAGE,
      "either `--routine <id>` or `--specialist <slug>` is required.\nRun: shipctl run --help",
    );
  }
  if (args.routine && args.specialist) {
    die(EXIT_USAGE, "`--routine` and `--specialist` are mutually exclusive.");
  }

  const cwd = args.cwd || process.cwd();
  const root = findShipRoot(cwd);
  if (!root) {
    die(EXIT_USAGE, `.ship/config.yml not found (searched from ${path.resolve(cwd)} upward).`);
  }

  const { config } = readConfig(cwd);
  // Post-E16, the backend dispatcher fires `workflow_dispatch` with
  // a concrete `(routine_id, ticket_ref)` pair — ``routine_id`` is
  // the bundle slug (``planning`` / ``developer`` / ``validation`` /
  // ``reviewer`` / ``decomposition`` / workspace ``self-heal`` /
  // ``daily-digest`` / ``weekly-audit``) and matches the agent-role
  // slug on the server. The customer's ``.ship/config.yml`` no longer
  // owns the schedule (the customer-side cron picker was retired in
  // ELS-124), so there's no ``process.routines`` map to look anything
  // up in. Both ``--routine`` and ``--specialist`` collapse to the
  // same synthetic executable: pass the slug straight through and let
  // ``GET /v1/.../agent-roles/{slug}/resolve`` decide whether it's a
  // real role.
  //
  // ``process.routines`` entries in legacy config files (preserved
  // for back-compat with repos that still carry overrides like a
  // custom inline prompt) take precedence when ``--routine`` matches
  // — that's the only path that still cracks open ``config``.
  const slug = args.specialist || args.routine;
  const override = !args.specialist ? resolveExecutable(config, args.routine) : null;
  const resolved = override || {
    kind: "specialist",
    id: slug,
    source: { specialist: slug },
    executable: {
      id: slug,
      type: "specialist",
      kind: "pipeline_pick",
      specialist: slug,
      prompt: null,
    },
  };

  // ``runId`` for emit/branch naming carries either the routine id or
  // the specialist slug. Logging downstream uses ``runHandle``.
  const runHandle = args.routine || `pipeline:${args.specialist}`;

  const env = readEnv();
  const { apiBase, apiToken, workspaceId, githubRepo } = env;

  // 1) Resolve specialist slug.
  // ``routine.specialist`` is the canonical Phase-2.4 form; the legacy
  // ``routine.pattern`` is mapped to a slug in
  // ``cli/lib/runtime/routines.mjs`` (drops the ``role-`` prefix).
  const specialistSlug = resolved.executable.specialist || args.specialist;
  if (!specialistSlug) {
    die(
      EXIT_USAGE,
      `routine '${args.routine}' has no 'specialist:' (or legacy 'pattern:') set`,
    );
  }
  if (!apiBase || !apiToken || !workspaceId) {
    die(
      EXIT_USAGE,
      "agent-role resolve requires SHIP_API_BASE + SHIP_API_TOKEN + SHIP_WORKSPACE_ID",
    );
  }

  step("resolve_args", "ok", {
    routine: args.routine,
    specialist: args.specialist,
    handle: runHandle,
  });

  // 2) Pull the resolved role + the shared system prompt in parallel.
  // ``resolveAgentRole`` returns the workspace override when present,
  // otherwise falls back to the Ship default. ``system`` is fetched
  // optional — older deployments may not have it; we render without
  // a system header in that case.
  const [roleResolved, systemResolved] = await Promise.all([
    resolveAgentRole({ apiBase, apiToken, workspaceId, slug: specialistSlug }),
    resolveAgentRole({
      apiBase,
      apiToken,
      workspaceId,
      slug: SYSTEM_ROLE_SLUG,
      optional: true,
    }),
  ]);
  if (!roleResolved) {
    die(EXIT_USAGE, `unknown agent role '${specialistSlug}' for this workspace`);
  }
  step("resolve_role", "ok", {
    specialist: specialistSlug,
    role_source: roleResolved.source,
    system_present: Boolean(systemResolved?.prompt),
    role_len: (roleResolved.prompt || "").length,
  });
  // Per-routine FSM stage override takes precedence over the role's
  // default. Lets one role (``ba``) drive both ``ba_requirements`` for
  // SDLC and ``wbs`` for decomposition without per-process role clones.
  const fsmStage = resolved.executable?.fsm_stage || roleResolved.fsm_stage || null;
  const roleBody = roleResolved.prompt || "";
  const systemBody = systemResolved?.prompt || "";

  // 3) Resolve task. ``--dry-run`` skips the server call and uses a
  // synthetic task so the operator can see the prompt shape without
  // needing the new endpoints deployed.
  //
  // Workspace-scope bundles (``self-heal`` / ``daily-digest`` /
  // ``weekly-audit``) carry an ``fsm_stage`` that starts with
  // ``workspace_``. Their role prompts have no ``{{ISSUE}}``
  // expectation — the agent operates on the workspace as a whole
  // (audit-log scans + corrective writes). Hitting ``/tracker/next``
  // for these would always return null and noop us out, so we skip
  // the picker entirely and feed the agent a synthetic
  // workspace-scope task.
  let task = null;
  const isWorkspaceScope =
    typeof fsmStage === "string" && fsmStage.startsWith("workspace_");
  if (isWorkspaceScope) {
    task = {
      ticket_ref: "",
      kind: "workspace",
      title: `Workspace bundle: ${specialistSlug}`,
      body: "",
      url: null,
      labels: [],
      state: "open",
      fsm_stage: fsmStage,
    };
    step("tracker_next", "skipped", {
      reason: "workspace_scope",
      fsm_stage: fsmStage,
    });
  } else if (fsmStage) {
    if (args.dryRun || ctx.dryRun) {
      task = {
        ticket_ref: "dry-run/sample#1",
        kind: "dry-run",
        title: "Sample ticket for dry-run prompt rendering",
        body: "This is a synthetic ticket body. The real one comes from `GET /tracker/next` when not in dry-run.",
        url: null,
        labels: ["sample"],
        state: "open",
        fsm_stage: fsmStage,
      };
    } else {
      task = await getNextTask({
        apiBase,
        apiToken,
        workspaceId,
        state: fsmStage,
        ticketRef: args.ticket,
      });
      if (!task) {
        step("tracker_next", "noop", { fsm_stage: fsmStage, reason: "no_eligible_ticket" });
        emit(args, {
          status: "noop",
          routine: args.routine,
          specialist: specialistSlug,
          fsm_stage: fsmStage,
          reason: "no_eligible_ticket",
          run_handle: runHandle,
        });
        process.exit(EXIT_NO_TASK);
      }
      step("tracker_next", "ok", {
        fsm_stage: fsmStage,
        ticket: task.ticket_ref,
        title_len: (task.title || "").length,
      });
    }
  } else {
    step("tracker_next", "skipped", { reason: "no_fsm_stage" });
  }

  // 4) Fetch the workspace policy preamble so the agent prompt
  // carries the same standing rules the Navigator chat does. Best-
  // effort: a missing token, missing API base, or a network failure
  // quietly skips the prepend — local / offline runs still work.
  // ``role`` is now the specialist slug directly (no more
  // ``spec.role`` indirection).
  const policiesPreamble = await fetchPoliciesPreamble({
    apiBase,
    apiToken,
    workspaceId,
    role: specialistSlug,
  });
  step("policies_preamble", policiesPreamble ? "ok" : "empty", {
    len: policiesPreamble ? policiesPreamble.length : 0,
  });

  // 5) Mint a run_id + render prompt with finish-protocol values
  // already substituted so the agent can call /agent-runs/finish from
  // inside Cursor without holding any extra config.
  const runId = `run_${crypto.randomBytes(8).toString("hex")}`;
  const renderedPrompt = renderPrompt({
    patternBody: roleBody,
    baseBody: systemBody,
    role: specialistSlug,
    routineSpec: resolved.executable,
    task,
    fsmStage,
    finishCtx: {
      apiBase,
      apiToken,
      workspaceId,
      runId,
      role: specialistSlug,
      ticketRef: task?.ticket_ref || null,
      fsmStage: fsmStage || null,
    },
  });
  const prompt = policiesPreamble
    ? `${policiesPreamble.trim()}\n\n---\n\n${renderedPrompt}`
    : renderedPrompt;

  // ``--dry-run`` exits here so the operator can eyeball the rendered
  // prompt + resolved task without launching an agent or touching any
  // tracker. Useful when iterating on prompts.
  if (args.dryRun || ctx.dryRun) {
    if (args.json) {
      console.log(JSON.stringify({
        status: "dry-run",
        routine: args.routine,
        specialist: specialistSlug,
        specialist_source: roleResolved.source,
        fsm_stage: fsmStage,
        task,
        prompt,
      }, null, 2));
    } else {
      console.error(`# ship: dry-run handle=${runHandle} specialist=${specialistSlug} (${roleResolved.source}) fsm_stage=${fsmStage || "(context-free)"}`);
      if (task) {
        console.error(`# ship: task ticket_ref=${task.ticket_ref} title=${JSON.stringify(task.title || "")}`);
      } else {
        console.error("# ship: task=(none)");
      }
      console.error("# ---- prompt ----");
      console.log(prompt);
    }
    process.exit(EXIT_OK);
  }

  // 4) Resolve the agent runtime. The workspace's bound provider
  // (``GET /v1/workspaces/{ws}/agent-provider``) is the single source
  // of truth — when it fails we fall straight back to the resolver's
  // built-in ``DEFAULT_PROVIDER`` (cursor) so an unreachable API
  // doesn't strand the runner. The legacy ``.ship/config.yml``
  // ``agent.default.provider`` / ``agent.overrides`` block was
  // dropped in PR-5 of the local-CLI swap.
  const workspaceProvider = await fetchWorkspaceAgentProvider({
    apiBase,
    apiToken,
    workspaceId,
  });
  const provider = workspaceProvider || DEFAULT_PROVIDER;
  step("resolve_provider", workspaceProvider ? "ok" : "default", { provider });
  const branchName = makeBranchName(runHandle, task?.ticket_ref);
  const baseBranch = (env.githubRef || "main").trim() || "main";

  // 4a) When the runner asked us to drive the full local-mode loop
  // (``--commit-and-pr``), prep the branch ourselves before invoking
  // the adapter so the agent's commits land on a fresh head off
  // ``baseBranch``. Local dev runs without the flag stay in cwd —
  // useful for prompt iteration without yanking the working tree.
  if (args.commitAndPr) {
    try {
      prepareGitBranch({ branchName, baseBranch });
      step("prepare_branch", "ok", { branch: branchName, base: baseBranch });
    } catch (err) {
      emit(args, {
        status: "error",
        routine: args.routine,
        specialist: specialistSlug,
        run_id: runId,
        run_handle: runHandle,
        stage: "prepare_branch",
        provider,
        branch: branchName,
        error: err instanceof Error ? err.message : String(err),
      });
      process.exit(EXIT_AGENT_FAIL);
    }
  }

  let runtime;
  step("launch_agent", "starting", {
    provider,
    branch: branchName,
    prompt_len: prompt.length,
  });
  try {
    runtime = await runAgent(provider, {
      workdir: process.cwd(),
      branchName,
      prompt,
    });
  } catch (err) {
    emit(args, {
      status: "error",
      routine: args.routine,
      specialist: specialistSlug,
      run_id: runId,
      run_handle: runHandle,
      stage: "launch_agent",
      provider,
      error: err instanceof Error ? err.message : String(err),
    });
    process.exit(EXIT_AGENT_FAIL);
  }

  // 4b) Post-runtime: read the agent's finish sidecar (ELS-120) and
  // own the push + PR + /agent-runs/finish sequence ourselves. Falling
  // back to the pre-ELS-120 flow (agent calls /finish directly) when
  // no sidecar is present keeps existing role prompts working during
  // rollout.
  let pushed = null;
  let prUrl = null;
  if (args.commitAndPr) {
    if (runtime.status !== "FINISHED") {
      emit(args, {
        status: "error",
        routine: args.routine,
        specialist: specialistSlug,
        run_id: runId,
        run_handle: runHandle,
        stage: "agent_runtime",
        provider,
        branch: branchName,
        runtime_status: runtime.status,
        exit_code: runtime.exitCode,
        error: `agent runtime ${runtime.status} (exit=${runtime.exitCode})`,
      });
      process.exit(EXIT_AGENT_FAIL);
    }
    step("agent_done", "ok", {
      provider,
      runtime_status: runtime.status,
      exit_code: runtime.exitCode,
    });

    const sidecar = readAgentFinishSidecar(root);

    if (sidecar) {
      // New flow — run.mjs owns finish. The branch and run-context
      // fields (run_id, fsm_stage, process, ticket_ref) are forced
      // from the runner's view of the world; agent-supplied values
      // would be drift-prone (especially run_id, which the agent
      // can't actually know — we mint it locally each tick).
      step("agent_finish_sidecar", "found", {
        outcome: sidecar.outcome,
        wants_pr: Boolean(sidecar.pr),
      });

      const basePayload = {
        run_id: runId,
        outcome: sidecar.outcome,
        fsm_stage: fsmStage,
        stage_next: sidecar.stage_next ?? null,
        ticket_ref: ticketRefFromSidecar(sidecar, task),
        process: sidecar.process || "development",
        comment: sidecar.comment ?? null,
        summary: sidecar.summary ?? null,
        description: sidecar.description ?? null,
        project_sections: Array.isArray(sidecar.project_sections)
          ? sidecar.project_sections
          : [],
        child_tickets: Array.isArray(sidecar.child_tickets)
          ? sidecar.child_tickets
          : [],
        payload:
          sidecar.payload && typeof sidecar.payload === "object"
            ? sidecar.payload
            : {},
      };

      // outcome != ready_next_step → no push, no PR. Just forward.
      if (sidecar.outcome !== "ready_next_step") {
        const res = await postAgentFinish({
          apiBase,
          apiToken,
          workspaceId,
          payload: basePayload,
          description: `finish ${sidecar.outcome}`,
        });
        step("finish_post", res.ok ? "ok" : "fail", {
          status: res.status,
          outcome: sidecar.outcome,
        });
        if (!res.ok) {
          emit(args, {
            status: "error",
            routine: args.routine,
            specialist: specialistSlug,
            run_id: runId,
            run_handle: runHandle,
            stage: "finish_post",
            provider,
            branch: branchName,
            error: `finish POST failed (HTTP ${res.status}) ${res.error || ""}`.trim(),
          });
          process.exit(EXIT_AGENT_FAIL);
        }
        emit(args, {
          status: "completed",
          routine: args.routine,
          specialist: specialistSlug,
          fsm_stage: fsmStage,
          ticket_ref: ticketRefFromSidecar(sidecar, task),
          agent_id: runtime.agentId,
          branch: runtime.branchName,
          provider,
          runtime_status: runtime.status,
          exit_code: runtime.exitCode,
          pushed: false,
          pr_url: null,
          run_id: runId,
          run_handle: runHandle,
          outcome: sidecar.outcome,
        });
        process.exit(EXIT_OK);
      }

      // outcome == ready_next_step. Two branches:
      //   - sidecar.pr === null|undefined: code-less finish (intake,
      //     BA, project-sections, tasks). Just POST /finish.
      //   - sidecar.pr is set: code-changing finish. Verify commits,
      //     push, gh pr create, then POST /finish with PR URL spliced
      //     into the comment.

      const wantsPr = Boolean(
        sidecar.pr && typeof sidecar.pr === "object" && (sidecar.pr.title || sidecar.pr.body),
      );

      if (!wantsPr) {
        const res = await postAgentFinish({
          apiBase,
          apiToken,
          workspaceId,
          payload: basePayload,
          description: "finish ready_next_step (no PR)",
        });
        step("finish_post", res.ok ? "ok" : "fail", { status: res.status });
        if (!res.ok) {
          emit(args, {
            status: "error",
            routine: args.routine,
            specialist: specialistSlug,
            run_id: runId,
            run_handle: runHandle,
            stage: "finish_post",
            provider,
            branch: branchName,
            error: `finish POST failed (HTTP ${res.status}) ${res.error || ""}`.trim(),
          });
          process.exit(EXIT_AGENT_FAIL);
        }
        emit(args, {
          status: "completed",
          routine: args.routine,
          specialist: specialistSlug,
          fsm_stage: fsmStage,
          ticket_ref: ticketRefFromSidecar(sidecar, task),
          agent_id: runtime.agentId,
          branch: runtime.branchName,
          provider,
          runtime_status: runtime.status,
          exit_code: runtime.exitCode,
          pushed: false,
          pr_url: null,
          run_id: runId,
          run_handle: runHandle,
          outcome: sidecar.outcome,
        });
        process.exit(EXIT_OK);
      }

      // PR-authoring path. If anything between here and the
      // /finish call fails, rewrite the outcome to ``blocked`` with
      // a structured reason and POST that — so the FSM doesn't
      // advance past a stage that never actually produced a PR.
      const rewriteBlocked = async (stageLabel, errMsg) => {
        const rewrittenComment = sidecar.comment
          ? `${sidecar.comment.trim()}\n\nrun.mjs: ${stageLabel} failed — ${errMsg}`
          : `run.mjs: ${stageLabel} failed — ${errMsg}`;
        const blockedPayload = {
          ...basePayload,
          outcome: "blocked",
          stage_next: null,
          comment: rewrittenComment,
          payload: {
            ...(basePayload.payload || {}),
            ship_runner: {
              rewrote_outcome: "ready_next_step → blocked",
              failing_stage: stageLabel,
              error: errMsg,
            },
          },
        };
        const res = await postAgentFinish({
          apiBase,
          apiToken,
          workspaceId,
          payload: blockedPayload,
          description: `finish blocked (${stageLabel})`,
        });
        step("finish_post_blocked", res.ok ? "ok" : "fail", {
          status: res.status,
          stage: stageLabel,
        });
      };

      if (!hasNewCommits(baseBranch)) {
        await rewriteBlocked(
          "verify_commits",
          "agent declared `pr` but branch has no commits vs main",
        );
        emit(args, {
          status: "error",
          routine: args.routine,
          specialist: specialistSlug,
          run_id: runId,
          run_handle: runHandle,
          stage: "verify_commits",
          provider,
          branch: branchName,
          error: "branch has no commits — outcome rewritten to blocked",
        });
        process.exit(EXIT_AGENT_FAIL);
      }

      // Mint the Ship App installation token BEFORE push. The
      // default ``${{ secrets.GITHUB_TOKEN }}`` that ``actions/
      // checkout`` configures has ``contents: write`` but cannot push
      // changes to ``.github/workflows/**`` — that's a GitHub-level
      // safety on the workflow token, not a permissions config we
      // can fix in YAML. The Ship App's installation token, on the
      // other hand, carries ``workflows: write`` (declared on the App
      // itself) and is allowed to push workflow files. We pass it
      // into ``pushBranch`` so the runner can land an agent commit
      // that touches CI plumbing without operator hand-rolled
      // configuration.
      const ghToken = await fetchInstallationToken({
        apiBase,
        apiToken,
        workspaceId,
        githubRepo: env.githubRepo,
      });
      step("mint_pr_token", ghToken ? "ok" : "fallback_env", {
        source: ghToken ? "ship_app" : "github_actions",
      });

      try {
        pushed = pushBranch({ branchName, ghToken, githubRepo: env.githubRepo });
        step("push_branch", "ok", { branch: branchName });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        await rewriteBlocked("push_branch", msg);
        emit(args, {
          status: "error",
          routine: args.routine,
          specialist: specialistSlug,
          run_id: runId,
          run_handle: runHandle,
          stage: "push_branch",
          provider,
          branch: branchName,
          error: msg,
        });
        process.exit(EXIT_AGENT_FAIL);
      }

      try {
        prUrl = openPullRequest({
          branchName,
          baseBranch,
          title:
            (sidecar.pr.title || "").trim() ||
            makePrTitle({ specialist: specialistSlug, fsmStage, task }),
          body: composePrBody({
            agentBody: sidecar.pr.body || "",
            task,
            runHandle,
          }),
          ghToken,
        });
        step("open_pr", prUrl ? "ok" : "skipped_gh_unavailable", { pr: prUrl || null });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        await rewriteBlocked("open_pr", msg);
        emit(args, {
          status: "error",
          routine: args.routine,
          specialist: specialistSlug,
          run_id: runId,
          run_handle: runHandle,
          stage: "open_pr",
          provider,
          branch: branchName,
          error: msg,
        });
        process.exit(EXIT_AGENT_FAIL);
      }

      if (!prUrl) {
        // ``gh`` binary absent on this runner — degrade gracefully
        // by surfacing the failure on the ticket. The branch is
        // pushed; an operator can open the PR manually if they care.
        await rewriteBlocked(
          "open_pr",
          "gh CLI unavailable on runner; branch pushed, manual PR needed",
        );
        emit(args, {
          status: "error",
          routine: args.routine,
          specialist: specialistSlug,
          run_id: runId,
          run_handle: runHandle,
          stage: "open_pr",
          provider,
          branch: branchName,
          error: "gh unavailable",
        });
        process.exit(EXIT_AGENT_FAIL);
      }

      // Splice PR URL into the comment so the audit-log row carries
      // the PR link without scraping the body.
      const finalPayload = {
        ...basePayload,
        comment: appendPrUrlToComment(basePayload.comment, prUrl),
      };
      const res = await postAgentFinish({
        apiBase,
        apiToken,
        workspaceId,
        payload: finalPayload,
        description: "finish ready_next_step (with PR)",
      });
      step("finish_post", res.ok ? "ok" : "fail", {
        status: res.status,
        pr: prUrl,
      });
      if (!res.ok) {
        emit(args, {
          status: "error",
          routine: args.routine,
          specialist: specialistSlug,
          run_id: runId,
          run_handle: runHandle,
          stage: "finish_post",
          provider,
          branch: branchName,
          pr_url: prUrl,
          error: `finish POST failed (HTTP ${res.status}) ${res.error || ""}`.trim(),
        });
        process.exit(EXIT_AGENT_FAIL);
      }
    } else if (PR_AUTHORING_STAGES.has(fsmStage || "")) {
      // No sidecar AND this is a code-changing stage. Pre-ELS-120
      // behaviour: the agent has already (we hope) called /finish
      // directly with its outcome. We push + open a PR with the
      // backwards-compat metadata-only body so existing prompts
      // that haven't been rewritten still produce a PR.
      step("agent_finish_sidecar", "missing_fallback", { stage: fsmStage });
      if (!hasNewCommits(baseBranch)) {
        step("post_run", "noop", { reason: "no_commits" });
        emit(args, {
          status: "noop_no_commits",
          routine: args.routine,
          specialist: specialistSlug,
          fsm_stage: fsmStage,
          ticket_ref: task?.ticket_ref || null,
          provider,
          branch: branchName,
          run_id: runId,
          run_handle: runHandle,
        });
        process.exit(EXIT_OK);
      }
      try {
        pushed = pushBranch({ branchName });
        step("push_branch", "ok", { branch: branchName });
        const ghToken = await fetchInstallationToken({
          apiBase,
          apiToken,
          workspaceId,
          githubRepo: env.githubRepo,
        });
        step("mint_pr_token", ghToken ? "ok" : "fallback_env", {
          source: ghToken ? "ship_app" : "github_actions",
        });
        prUrl = openPullRequest({
          branchName,
          baseBranch,
          title: makePrTitle({ specialist: specialistSlug, fsmStage, task }),
          body: composePrBody({
            agentBody: `Autonomous agent run via Ship pipeline (no sidecar — pre-ELS-120 prompt).\n\n- specialist: \`${specialistSlug}\`\n- provider: \`${provider}\``,
            task,
            runHandle,
          }),
          ghToken,
        });
        step("open_pr", prUrl ? "ok" : "skipped_gh_unavailable", { pr: prUrl || null });
      } catch (err) {
        emit(args, {
          status: "error",
          routine: args.routine,
          specialist: specialistSlug,
          run_id: runId,
          run_handle: runHandle,
          stage: pushed ? "open_pr" : "push_branch",
          provider,
          branch: branchName,
          error: err instanceof Error ? err.message : String(err),
        });
        process.exit(EXIT_AGENT_FAIL);
      }
    } else {
      // No sidecar, non-PR stage — leave to the agent's direct
      // /finish call. Nothing for run.mjs to do here.
      step("agent_finish_sidecar", "missing_codeless", { stage: fsmStage });
    }
  }

  emit(args, {
    status: "completed",
    routine: args.routine,
    specialist: specialistSlug,
    fsm_stage: fsmStage,
    ticket_ref: task?.ticket_ref || null,
    agent_id: runtime.agentId,
    branch: runtime.branchName,
    provider,
    runtime_status: runtime.status,
    exit_code: runtime.exitCode,
    pushed: Boolean(pushed),
    pr_url: prUrl,
    run_id: runId,
    run_handle: runHandle,
  });
  process.exit(EXIT_OK);
}


// ---------------------------------------------------------------------------
// Git + PR helpers (used only when ``--commit-and-pr`` is set, i.e. the
// workflow runner mode). Kept inline rather than in a separate module so
// dev runs without ``--commit-and-pr`` don't pull these into the import
// graph at all — local prompt iteration shouldn't shell out to git.
// ---------------------------------------------------------------------------


function git(args, { capture = false } = {}) {
  const res = spawnSync("git", args, {
    stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
    encoding: "utf8",
  });
  if (res.status !== 0) {
    const stderr = capture ? (res.stderr || "").trim() : "";
    throw new Error(`git ${args.join(" ")} failed (exit=${res.status}) ${stderr}`);
  }
  return capture ? (res.stdout || "").trim() : "";
}


function prepareGitBranch({ branchName, baseBranch }) {
  // Only set committer identity if the runner hasn't already (idempotent
  // — overwriting a workspace-level config is fine on an ephemeral CI
  // runner; on dev this branch is unreachable because --commit-and-pr
  // isn't passed).
  spawnSync("git", ["config", "user.email", "ship-agent@elmundi.com"]);
  spawnSync("git", ["config", "user.name", "Ship Agent"]);
  // Make sure we're freshly on baseBranch, not on whatever branch the
  // previous step left us on. The runner is one-shot but we still
  // hard-fail loudly if HEAD is dirty (uncommitted changes from a
  // previous tick → broken cache pollution).
  const status = git(["status", "--porcelain"], { capture: true });
  if (status) {
    throw new Error(`working tree dirty before agent run: ${status.split("\n")[0]}`);
  }
  git(["checkout", "-B", branchName, baseBranch]);
}


function hasNewCommits(baseBranch) {
  // ``git rev-list --count baseBranch..HEAD`` returns 0 when no commits
  // were made on top of base. We use this instead of comparing diffs
  // because the agent might have committed an empty change (rare but
  // possible) — we still want to push that for traceability.
  try {
    const out = git(["rev-list", "--count", `${baseBranch}..HEAD`], { capture: true });
    return Number(out || "0") > 0;
  } catch {
    return false;
  }
}


function pushBranch({ branchName, ghToken, githubRepo }) {
  // When the Ship App installation token is available, push through
  // it directly so workflow-file changes (``.github/workflows/**``)
  // survive. The default ``${{ secrets.GITHUB_TOKEN }}`` that
  // ``actions/checkout`` configures into the credential helper
  // carries ``contents: write`` but is denied on workflow file
  // updates: ``refusing to allow a GitHub App to create or update
  // workflow ... without `workflows` permission`` — and that
  // permission can't be granted to the workflow token, only to the
  // App itself. The Ship App's installation token has
  // ``workflows: write`` declared on the App and is the right
  // identity for the push.
  //
  // We embed the token into a one-shot URL passed straight to
  // ``git push`` (bypassing the persisted ``origin`` remote +
  // credential helper that ``actions/checkout`` set up) so:
  //   - the token never lands in ``.git/config`` on disk;
  //   - there's no ordering hazard with the credential helper —
  //     ``git push <url>`` ignores the remote's auth entirely;
  //   - we don't need to undo any state on failure.
  // ``x-access-token`` is GitHub's documented username for App
  // installation tokens on HTTPS Git endpoints; the token itself
  // goes in the password slot.
  if (ghToken && githubRepo) {
    const repo = String(githubRepo).trim();
    const remoteUrl = `https://x-access-token:${ghToken}@github.com/${repo}.git`;
    // ``actions/checkout`` writes a per-URL ``http.extraheader`` into
    // ``.git/config`` with the workflow token; when we push via an
    // explicit URL, git STILL sends that header (URL-prefix match),
    // and GitHub then refuses the workflow-file write under the
    // workflow token's identity, ignoring the App token in the URL.
    // Strip the header for this push so the App-token credentials
    // win. ``--unset-all`` no-ops cleanly if the header isn't set.
    spawnSync(
      "git",
      [
        "config",
        "--local",
        "--unset-all",
        "http.https://github.com/.extraheader",
      ],
      { stdio: "ignore" },
    );
    git(["push", remoteUrl, `${branchName}:${branchName}`]);
  } else {
    git(["push", "-u", "origin", branchName]);
  }
  return branchName;
}


function openPullRequest({ branchName, baseBranch, title, body, ghToken }) {
  // ``gh pr create`` is the cheapest path. The default
  // ``GITHUB_TOKEN`` from ``actions/checkout`` can't open PRs unless
  // the org enables the "Allow GitHub Actions to create PRs" toggle
  // — most orgs leave that off. When ``ghToken`` is set (Ship App
  // installation token, fetched from
  // ``POST /repos/{repo_id}/installation-token``), use it as
  // ``GH_TOKEN`` for the spawn — the App token isn't subject to that
  // gate. ``gh`` picks up ``GH_TOKEN`` from env.
  const probe = spawnSync("gh", ["--version"], { stdio: "ignore" });
  if (probe.status !== 0) return null;
  const env = ghToken ? { ...process.env, GH_TOKEN: ghToken } : process.env;
  const res = spawnSync(
    "gh",
    [
      "pr",
      "create",
      "--base",
      baseBranch,
      "--head",
      branchName,
      "--title",
      title,
      "--body",
      body,
    ],
    { encoding: "utf8", env },
  );
  if (res.status !== 0) {
    throw new Error(
      `gh pr create failed (exit=${res.status}) ${(res.stderr || "").trim()}`,
    );
  }
  return (res.stdout || "").trim();
}


/**
 * Mint a Ship-App installation token for the runner's repo via the
 * Ship server, so ``gh pr create`` doesn't need the org's
 * ``GITHUB_TOKEN``-can-open-PRs toggle. ``githubRepo`` is the
 * ``owner/repo`` string from ``$GITHUB_REPOSITORY``; we round-trip
 * through ``GET /v1/workspaces/{ws}/repos`` to find the Ship repo
 * UUID before minting. Returns ``null`` on any failure so the caller
 * falls back to GHA's default token (which may still work if the
 * toggle is on).
 */
async function fetchInstallationToken({ apiBase, apiToken, workspaceId, githubRepo }) {
  if (!apiBase || !apiToken || !workspaceId || !githubRepo) return null;

  let listRes;
  try {
    listRes = await fetchWithRetry(
      () =>
        fetch(
          `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/repos`,
          {
            headers: {
              Accept: "application/json",
              Authorization: `Bearer ${apiToken}`,
            },
          },
        ),
      { description: "list-activated-repos" },
    );
  } catch {
    return null;
  }
  if (!listRes.ok) return null;
  let repos;
  try {
    repos = await listRes.json();
  } catch {
    return null;
  }
  const wanted = githubRepo.trim().toLowerCase();
  const repo = (Array.isArray(repos) ? repos : []).find(
    (r) => (r.full_name || "").toLowerCase() === wanted,
  );
  if (!repo || !repo.id) return null;

  let res;
  try {
    res = await fetchWithRetry(
      () =>
        fetch(
          `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repo.id)}/installation-token`,
          {
            method: "POST",
            headers: {
              Accept: "application/json",
              Authorization: `Bearer ${apiToken}`,
            },
          },
        ),
      { description: "installation-token" },
    );
  } catch {
    return null;
  }
  if (!res.ok) return null;
  try {
    const body = await res.json();
    return body.token || null;
  } catch {
    return null;
  }
}


// ---------------------------------------------------------------------------
// Sidecar-driven finish (ELS-120)
// ---------------------------------------------------------------------------
//
// Pre-ELS-120 the agent called ``POST /agent-runs/finish`` directly from
// inside its Cursor/Claude session. That happens *before* run.mjs gets a
// chance to push the branch and open the PR — if either of those later
// steps failed, the ticket was already moved forward in the FSM with no
// PR existing. The next stage agent (qa_manual) then had nothing to QA
// against and the ticket silently stalled.
//
// New flow: the agent writes its intended finish payload to a sidecar
// JSON file at ``.ship/agent-finish.json`` inside the repo workdir and
// stops without hitting the API. ``run.mjs`` reads the sidecar after the
// session ends, owns the push + PR + finish sequence, and rewrites the
// outcome to ``blocked`` if push/PR fails — so the FSM transition is
// gated on a real PR existing.
//
// Backwards compat: when no sidecar is found, ``run.mjs`` leaves the
// pre-existing flow intact (any direct ``/finish`` call the agent made
// stands). Backend enforces a stricter rule for code-changing stages
// (dev_implementation / qa_automation / workflow_self_heal): a
// ``ready_next_step`` finish there must carry a PR URL in ``comment``,
// otherwise it's 422'd — defends against rogue agents bypassing the
// sidecar protocol.

const AGENT_FINISH_SIDECAR_PATH = ".ship/agent-finish.json";

const PR_AUTHORING_STAGES = new Set([
  "dev_implementation",
  "qa_automation",
  "workflow_self_heal",
]);

function readAgentFinishSidecar(repoRoot) {
  const sidecarPath = path.join(repoRoot, AGENT_FINISH_SIDECAR_PATH);
  if (!fs.existsSync(sidecarPath)) return null;
  let raw;
  try {
    raw = fs.readFileSync(sidecarPath, "utf8");
  } catch (err) {
    console.error(`warn: agent-finish sidecar read failed: ${err.message}`);
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    console.error(`warn: agent-finish sidecar parse failed: ${err.message}`);
    return null;
  }
  if (!parsed || typeof parsed !== "object" || !parsed.outcome) {
    console.error("warn: agent-finish sidecar malformed (no outcome field)");
    return null;
  }
  return parsed;
}

async function postAgentFinish({ apiBase, apiToken, workspaceId, payload, description }) {
  // Idempotent on (run_id, outcome) server-side; the GHA cron retries
  // a failed POST without double-applying tracker side-effects.
  let res;
  try {
    res = await fetchWithRetry(
      () =>
        fetch(`${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-runs/finish`, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            Authorization: `Bearer ${apiToken}`,
          },
          body: JSON.stringify(payload),
        }),
      { description: description || "agent-runs/finish" },
    );
  } catch (err) {
    return {
      ok: false,
      status: 0,
      error: err instanceof Error ? err.message : String(err),
    };
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  return { ok: res.ok, status: res.status, body };
}

function appendPrUrlToComment(comment, prUrl) {
  // The agent composed the comment before run.mjs minted the PR URL,
  // so we splice the URL in. Try to keep the trailing role marker on
  // its own last line so the audit-log grep (``[Ship SDLC:role-...]``)
  // still works for downstream tooling.
  if (!prUrl) return comment || null;
  const existing = (comment || "").trim();
  const prLine = `PR: ${prUrl}`;
  if (!existing) return prLine;
  if (existing.includes(prUrl)) return existing;
  const markerMatch = existing.match(/(\n?\[Ship [^\]]+])\s*$/);
  if (markerMatch) {
    const beforeMarker = existing.slice(0, markerMatch.index).trimEnd();
    return `${beforeMarker}\n\n${prLine}\n\n${markerMatch[1].trim()}`;
  }
  return `${existing}\n\n${prLine}`;
}

function composePrBody({ agentBody, task, runHandle }) {
  // Trust the agent for the body's substance — Summary / Test plan /
  // anything else they want to surface to the reviewer agent. We tack
  // on a footer with the Closes-link (so GitHub closes the linked
  // tracker reference on merge for trackers that support that idiom)
  // and the Ship run handle (so we can grep audit logs back to the
  // launching tick). Footer is appended after the agent's body
  // verbatim — no merge / dedupe, agents shouldn't have to remember
  // to omit either line.
  const body = (agentBody || "").trim();
  const footerLines = [];
  if (task?.ticket_ref) {
    footerLines.push(`Closes ${task.ticket_ref}`);
  }
  footerLines.push("");
  footerLines.push("---");
  footerLines.push(`_Ship pipeline run · \`${runHandle}\`_`);
  const footer = footerLines.join("\n");
  if (!body) return footer;
  return `${body}\n\n${footer}`;
}

function ticketRefFromSidecar(sidecar, task) {
  return sidecar?.ticket_ref || task?.ticket_ref || null;
}


function makePrTitle({ specialist, fsmStage, task }) {
  const ticket = task?.ticket_ref ? ` ${task.ticket_ref}` : "";
  const stage = fsmStage ? ` · ${fsmStage}` : "";
  return `agent: ${specialist}${stage}${ticket}`;
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function readEnv() {
  return {
    apiBase: stripSlash(process.env.SHIP_API_BASE || ""),
    apiToken: process.env.SHIP_API_TOKEN || "",
    workspaceId: process.env.SHIP_WORKSPACE_ID || "",
    githubRepo: process.env.GITHUB_REPOSITORY || "",
    githubRef: (process.env.GITHUB_REF_NAME || "main").trim(),
  };
}


function stripSlash(s) {
  return s.replace(/\/+$/, "");
}


/**
 * Resolve an agent role through the workspace endpoint.
 *
 * Returns ``{slug, name, prompt, fsm_stage, source}`` (workspace row
 * or Ship default), ``null`` for 404, throws on auth/network errors
 * unless ``optional`` is set (then 404 *and* errors return ``null``
 * with a one-line warning).
 */
async function resolveAgentRole({
  apiBase,
  apiToken,
  workspaceId,
  slug,
  optional = false,
}) {
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-roles/${encodeURIComponent(slug)}/resolve`;
  let res;
  try {
    res = await fetchWithRetry(
      () =>
        fetch(url, {
          headers: {
            Accept: "application/json",
            Authorization: `Bearer ${apiToken}`,
          },
        }),
      { description: `agent-role '${slug}' resolve` },
    );
  } catch (err) {
    if (optional) {
      console.error(
        `warn: agent-role '${slug}' resolve failed (network): ${err instanceof Error ? err.message : err}`,
      );
      return null;
    }
    throw err;
  }
  if (res.status === 404) return null;
  if (!res.ok) {
    if (optional) {
      console.error(
        `warn: agent-role '${slug}' resolve returned ${res.status}; running without it`,
      );
      return null;
    }
    throw new Error(
      `agent-roles resolve ${res.status}: ${(await res.text()).slice(0, 300)}`,
    );
  }
  return res.json();
}


/**
 * Best-effort fetch of the workspace's policy preamble. Returns the
 * markdown block to prepend, or ``null`` when there's nothing to
 * inject (no policies, missing token / API base, network error,
 * non-200 response). Never throws — a broken policies path mustn't
 * break a routine run.
 *
 * Auth: workspace-membership token (``SHIP_API_TOKEN`` — same one
 * the rest of ``run.mjs`` uses). The companion run-token endpoint
 * at ``/v1/runs/{run_id}/policies-preamble`` doesn't fit
 * this flow because the CLI mints ``run_id`` locally; the
 * workspace-scoped variant takes membership instead.
 */
async function fetchPoliciesPreamble({ apiBase, apiToken, workspaceId, role }) {
  if (!apiBase || !apiToken || !workspaceId) return null;
  const qs = role ? `?role=${encodeURIComponent(role)}` : "";
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/policies/preamble${qs}`;
  let res;
  try {
    res = await fetchWithRetry(
      () =>
        fetch(url, {
          headers: {
            Accept: "application/json",
            Authorization: `Bearer ${apiToken}`,
          },
        }),
      { description: "policies preamble fetch" },
    );
  } catch (err) {
    console.error(
      `warn: policies preamble fetch failed (network): ${err instanceof Error ? err.message : err}`,
    );
    return null;
  }
  if (!res.ok) {
    // 404 happens against older backends that don't have the
    // workspace-scoped endpoint yet — silent skip there. Other
    // statuses (401, 403, 500) get a one-line warning so operators
    // notice misconfigurations without aborting the run.
    if (res.status !== 404) {
      console.error(
        `warn: policies preamble fetch returned ${res.status}; running without preamble`,
      );
    }
    return null;
  }
  let body;
  try {
    body = await res.json();
  } catch {
    return null;
  }
  return typeof body?.preamble === "string" && body.preamble.trim()
    ? body.preamble
    : null;
}


async function getNextTask({ apiBase, apiToken, workspaceId, state, ticketRef }) {
  const params = new URLSearchParams({ state });
  if (ticketRef) params.set("ticket_ref", ticketRef);
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/tracker/next?${params}`;
  const res = await fetchWithRetry(
    () =>
      fetch(url, {
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${apiToken}`,
        },
      }),
    { description: `tracker/next?state=${state}` },
  );
  if (!res.ok) {
    throw new Error(`tracker/next ${res.status}: ${(await res.text()).slice(0, 300)}`);
  }
  const body = await res.json();
  return body.ticket || null;
}


/**
 * Read the workspace's bound agent provider via
 * ``GET /v1/workspaces/{ws}/agent-provider``. Returns ``null`` on
 * any failure so the caller can degrade to ``DEFAULT_PROVIDER``
 * rather than stranding the runner.
 */
async function fetchWorkspaceAgentProvider({ apiBase, apiToken, workspaceId }) {
  if (!apiBase || !apiToken || !workspaceId) return null;
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-provider`;
  let res;
  try {
    res = await fetchWithRetry(
      () =>
        fetch(url, {
          headers: {
            Accept: "application/json",
            Authorization: `Bearer ${apiToken}`,
          },
        }),
      { description: "agent-provider" },
    );
  } catch {
    return null;
  }
  if (!res.ok) return null;
  try {
    const body = await res.json();
    return body.kind || null;
  } catch {
    return null;
  }
}


function renderPrompt({ patternBody, baseBody, role, routineSpec, task, fsmStage, finishCtx }) {
  const issueRef = task?.ticket_ref ? task.ticket_ref : "(no ticket)";
  const title = task?.title || "";
  const description = task?.body || "";

  // Pattern-template substitution. Order matters: expand {{BASE}} first
  // so any further {{ROLE}} / {{SKILLS_CONTEXT}} placeholders inside
  // common-base also get resolved.
  const baseExpanded = (baseBody || "")
    .replace(/\{\{ROLE\}\}/g, role)
    .replace(/\{\{ISSUE\}\}/g, issueRef)
    .replace(/\{\{SKILLS_CONTEXT\}\}/g, "(no skills directory bundled in this run)");

  const expanded = patternBody
    .replace(/\{\{BASE\}\}/g, baseExpanded)
    .replace(/\{\{ROLE\}\}/g, role)
    .replace(/\{\{ISSUE\}\}/g, issueRef)
    .replace(/\{\{TITLE\}\}/g, title.slice(0, 500))
    .replace(/\{\{DESCRIPTION\}\}/g, description.slice(0, 8000));

  const out = [];
  if (routineSpec.prompt) {
    out.push("## Routine instructions");
    out.push(routineSpec.prompt.trim());
    out.push("");
  }
  out.push(expanded.trim());
  out.push("");
  out.push(renderLifecycleHooks());
  if (task) {
    // ELS-86: parent project context (Brief / WBS / Architecture /
    // Test architecture / Tasks). The server lifts and caps it; we
    // render it BEFORE the per-ticket block so the agent sees the
    // surrounding plan first, then narrows to its own scope. Only
    // present when the ticket is part of a decomposed project — the
    // server returns ``project_context: null`` otherwise and we
    // skip the block silently.
    if (typeof task.project_context === "string" && task.project_context.trim()) {
      out.push("");
      out.push("## Project context");
      out.push("");
      out.push(
        "_Excerpt of the parent project body. Read for surrounding plan;",
        "your scope is the per-task block below, not the whole project._",
      );
      out.push("");
      out.push(task.project_context.trim());
    }
    out.push("");
    out.push("## Task");
    out.push(`- **Ticket:** \`${task.ticket_ref}\` (${task.kind})`);
    if (task.url) out.push(`- **URL:** ${task.url}`);
    if (task.title) out.push(`- **Title:** ${task.title}`);
    if (task.fsm_stage || fsmStage) out.push(`- **FSM stage:** \`${task.fsm_stage || fsmStage}\``);
    if (Array.isArray(task.labels) && task.labels.length) {
      out.push(`- **Labels:** ${task.labels.join(", ")}`);
    }
    if (task.body) {
      out.push("");
      out.push("### Description");
      out.push(task.body);
    }
  }
  out.push("");
  out.push(renderExitProtocol(finishCtx));
  return out.join("\n");
}


function renderLifecycleHooks() {
  // Phase 4: every run is bracketed by two auditable lifecycle hooks
  // — knowledge-fetch first, knowledge-feedback last. We render them
  // as explicit prompt instructions so they show up in the agent's
  // tool-use log (the runner audits the log to flag runs that
  // skipped them). Soft for now (no run-blocking enforcement) but
  // the prompt is the canonical contract until the runner grows
  // tool-stream interception.
  return [
    "## Lifecycle hooks (Phase 4)",
    "",
    "**First call — knowledge fetch.** Before any other tool call,",
    `run \`shipctl knowledge fetch <bucket>\` for at least one bucket`,
    "relevant to your role. The audit log flags runs whose first",
    "tool call wasn't a knowledge fetch; do not skip this to save",
    "tokens.",
    "",
    "**Last call — knowledge feedback.** Before calling the finish",
    "endpoint, leave one-line learnings via the Ship knowledge",
    "feedback channel (`shipctl feedback draft` → `feedback submit`)",
    "if you discovered something a future run on this codebase would",
    "want to know. Empty findings are a valid outcome — better no",
    "feedback than fabricated polish.",
  ].join("\n");
}


function renderExitProtocol(ctx) {
  const ticketRef = ctx?.ticketRef ?? null;
  const ticketLine = ticketRef === null
    ? '"ticket_ref": null,'
    : `"ticket_ref": ${JSON.stringify(ticketRef)},`;
  const isPrAuthoringRole = ["developer", "qa-automation", "workflow-self-heal"]
    .includes(ctx?.role || "");

  return `## Required exit protocol

**Do not call Ship's finish API directly.** Write your finish payload
to \`.ship/agent-finish.json\` in the repo workdir and stop. The Ship
runner (\`run.mjs\`) reads this sidecar after your session ends,
handles push + \`gh pr create\` for code-changing roles, and posts
the \`/agent-runs/finish\` call on your behalf — splicing the PR URL
into your \`comment\` on success or rewriting your outcome to
\`blocked\` if push/PR fails.

Why: your session terminates **before** the runner can push your
branch or open the PR. If you call \`/finish\` directly with
\`ready_next_step\`, the ticket advances in the FSM even when push
or PR-create fails seconds later — the next stage's agent then has
nothing to QA against and the ticket stalls silently.

**Do not** create empty branches or commit placeholder files. If
your role doesn't change code, leave \`pr\` null in the sidecar and
do no git work at all. Reading via MCP is fine; writing through any
non-sidecar path is not.

\`\`\`json
{
  "outcome": "ready_next_step",
  "stage_next": "<next FSM stage, e.g. ba_requirements>",
  ${ticketLine}
  "process": "development",
  "comment": "<short audit narration ending with [Ship SDLC:${ctx?.role || "{{ROLE}}"}]>",
  "description": ${
    isPrAuthoringRole
      ? "null"
      : "\"<Full rewritten ticket body when your role shapes the ticket (intake, BA, planner). null otherwise.>\""
  },
  "summary": null,
  "project_sections": [],
  "child_tickets": [],
  "payload": {},
  "pr": ${
    isPrAuthoringRole
      ? `{
    "title": "feat(${ticketRef || "<TICKET>"}): <one-line headline>",
    "body": "## Summary\\n<2-4 lines>\\n\\n## Test plan\\n- [ ] <verification step>"
  }`
      : "null"
  }
}
\`\`\`

The runner pre-fills \`run_id\` and \`fsm_stage\` from the routine
context — don't include them in the sidecar (any values you provide
are dropped to prevent drift). The branch name is also runner-fixed
(\`fix/<TICKET>-auto\`).

### \`pr\` field (code-changing roles only)

Set \`pr\` to an object with \`title\` and \`body\` ONLY when your
role authors code (developer / qa-automation / workflow-self-heal).
The runner pushes your branch, opens the PR with your \`title\` /
\`body\`, appends a \`Closes <ticket>\` footer + run-handle line, and
splices \`PR: <url>\` into your \`comment\`.

If push or \`gh pr create\` fails, the runner rewrites the outcome
to \`blocked\` with a structured reason — you don't need to
defensively choose \`blocked\` yourself.

Every other role leaves \`pr: null\` and skips push entirely.

### \`project_sections\` (decomposition only)

When your role text says you own a **project body section** (\`## WBS\`,
\`## Architecture\`, \`## Test architecture\`, \`## Tasks\`, …), put your
output here as a **top-level** \`project_sections\` array — NOT inside
the \`payload\` dict. Server resolves the anchor's project_id and
upserts only your section, leaving the others untouched.

Shape:

\`\`\`json
"project_sections": [
  { "section": "Test architecture", "body": "<your full markdown>" }
]
\`\`\`

The runner posts \`/finish\` once on your behalf and surfaces the
server's \`actions\` list in its stdout. The list will include
\`tracker:project_section:<Section>\` when the upsert applied.

For SDLC (non-decomposition) roles, leave the array empty.

### \`child_tickets\` (decomposition \`tasks\` stage only)

The developer at the \`tasks\` stage carves the project's WBS into
one tracker ticket per slice. Declare each child here; the server
creates them under the anchor's project (via the tracker's
\`create_ticket\` adapter), then auto-renders a \`## Tasks\` section
listing the freshly-created identifiers — you cannot guess the
identifiers up-front, and you should NOT also send a
\`project_sections=[{Tasks,…}]\` entry (the auto-rendered list
overwrites yours by design).

Shape:

\`\`\`json
"child_tickets": [
  { "title": "<WBS slice name>", "body": "<3–5 line scope>" },
  …
]
\`\`\`

Server response \`actions\` will include one
\`tracker:ticket_created:<id>\` per child plus
\`tracker:project_section:Tasks\` for the auto-rendered index.

Every other role leaves the array empty.

### Outcomes

- **\`ready_next_step\`** — your role finished cleanly. Two shapes:

  1. **You worked on a ticket.** Set \`ticket_ref\` and \`stage_next\`
     to the next FSM stage; server moves the ticket. If your role's
     job is to shape the ticket (intake / BA / planner), set
     \`description\` to the **full rewritten body** — the server
     replaces the tracker description (Linear keeps the prior body
     in the activity feed, so nothing is lost). Use \`comment\` for
     a short audit narration of what changed and why; **do not put
     the new spec text in a comment**, otherwise the ticket
     description rots while comments accumulate. Pure-narration
     roles (security-officer, retro) skip \`description\` entirely.
  2. **There was nothing to do.** Pass \`ticket_ref: null\` and omit
     \`stage_next\`. The server records the run in the audit log and
     does **nothing** else — no inbox row, no tracker mutation. This
     is the right outcome when a context-free routine (daily audit,
     security sweep, retro) found no findings, OR when an FSM-stage
     agent picked up no eligible ticket. **No work is not a blocker.**

- **\`needs_clarification\`** — you're waiting on a human. Set
  \`comment\` with the question (server posts it) or omit it if you
  already left the question via a separate read-only path. Server
  tags the ticket \`needs:clarification\` so intake stops re-picking.
  Status stays Todo. \`stage_next\` is ignored. Requires a
  \`ticket_ref\`.

- **\`blocked\`** — **the environment is broken.** Use this only when
  something on the runner side prevents the work from running:
  missing secret, dead adapter, conflicting branch, tracker
  unreachable, snyk/probe binary not installed, etc. Server drops a
  blocker row into the inbox so an operator can fix the plumbing.
  **Do not use \`blocked\` to mean "no findings" or "queue empty"**
  — those are \`ready_next_step\` with \`ticket_ref: null\`.

- **\`out_of_scope\`** — the ticket is invalid or shouldn't be
  processed. Server moves it to Done with optional \`comment\`.
  \`stage_next\` is ignored. Requires a \`ticket_ref\`.

### Security

\`SHIP_API_TOKEN\` is rendered into this prompt for read-only Ship
API access during the session (ticket snapshots, project listings,
audit-log lookups). **Do not echo it back into commit messages, PR
descriptions, comments, logs, or any output you produce.** Treat it
as a one-shot credential for this run.
`;
}


function makeBranchName(routine, ticketRef) {
  const stamp = Date.now().toString(36);
  // Sanitize ``routine`` too — for pipeline-pick runs ``runHandle`` is
  // ``pipeline:<specialist>`` and the bare ``:`` is in git's reserved
  // character set, which Cursor's ``/v0/agents`` validator rejects
  // with HTTP 400 ("Invalid branch name. Branch names cannot start
  // with '-', contain invalid characters (spaces, ~, ^, :, ?, *, [,
  // ], \\, .., @{, //), end with '/', '.lock', or '.', or be named
  // 'HEAD'."). Same regex as the ticketRef path.
  const safeRoutine = String(routine).replace(/[^a-zA-Z0-9_-]/g, "-");
  if (ticketRef) {
    const safe = String(ticketRef).replace(/[^a-zA-Z0-9_-]/g, "-");
    return `cursor/ship-${safeRoutine}-${safe}-${stamp}`;
  }
  return `cursor/ship-${safeRoutine}-${stamp}`;
}


// ---------------------------------------------------------------------------
// Argument plumbing
// ---------------------------------------------------------------------------


function parseArgs(rest) {
  const out = {
    routine: null,
    specialist: null,
    ticket: null,
    cwd: null,
    json: false,
    help: false,
    dryRun: false,
    commitAndPr: false,
    debug: false,
    statusFile: null,
  };
  const copy = [...rest];
  while (copy.length) {
    const a = copy[0];
    if (a === "--help" || a === "-h") { out.help = true; copy.shift(); continue; }
    if (a === "--json") { out.json = true; copy.shift(); continue; }
    if (a === "--dry-run") { out.dryRun = true; copy.shift(); continue; }
    if (a === "--commit-and-pr") { out.commitAndPr = true; copy.shift(); continue; }
    if (a === "--debug") { out.debug = true; copy.shift(); continue; }
    if (a === "--routine" && copy[1] !== undefined) { out.routine = copy[1]; copy.splice(0, 2); continue; }
    if (a === "--specialist" && copy[1] !== undefined) { out.specialist = copy[1]; copy.splice(0, 2); continue; }
    // E16/ELS-124: pin the picker to one ticket. The backend
    // dispatcher writes both ``routine_id`` and ``ticket_ref`` into
    // the workflow inputs because we want the agent to act on the
    // EXACT ticket whose state transition fired the dispatch — not
    // whichever other ticket happens to be eligible for the routine.
    if (a === "--ticket" && copy[1] !== undefined) { out.ticket = copy[1]; copy.splice(0, 2); continue; }
    if (a === "--cwd" && copy[1] !== undefined) { out.cwd = path.resolve(copy[1]); copy.splice(0, 2); continue; }
    if (a === "--status-file" && copy[1] !== undefined) { out.statusFile = path.resolve(copy[1]); copy.splice(0, 2); continue; }
    // Soft-ignore legacy flags that older trigger workflows still pass —
    // the new pipeline doesn't need them and refusing would break repos
    // that haven't re-seeded yet. ``--lane`` is the back-compat spelling
    // of ``--routine`` from before the rename.
    if (a === "--trigger" && copy[1] !== undefined) { copy.splice(0, 2); continue; }
    if (a === "--lane" && copy[1] !== undefined) { out.routine = copy[1]; copy.splice(0, 2); continue; }
    die(EXIT_USAGE, `unknown argument: ${a}`);
  }
  return out;
}


function printHelp() {
  console.log(`shipctl run — execute one E14 routine or pipeline-pick specialist end-to-end.

Run
  shipctl run --routine <id> [--json] [--cwd <dir>] [--dry-run]
  shipctl run --specialist <slug> [--json] [--cwd <dir>] [--dry-run]

  --routine     id from process.routines in .ship/config.yml (cron-driven)
  --specialist  agent-role slug from the Ship registry (pipeline-pick fallback)
  --status-file <path>  also write the final status JSON to this file (the
                        same payload as --json stdout). The schedule
                        workflow uses this to loop through routines and
                        skip past noops without parsing stderr.

DEBUG
  --debug       stream a single-line per-step log to stderr (resolve_role,
                tracker_next, policies_preamble, resolve_provider,
                prepare_branch, launch_agent, push_branch, open_pr).
                Useful for workflow_dispatch debug runs — the GHA log
                shows every phase of the pipeline interleaved with the
                agent CLI's own output.

ENV
  SHIP_API_BASE        Ship server base URL (e.g. https://api.ship.elmundi.com)
  SHIP_API_TOKEN       workspace API token; rendered into the agent prompt
                       so the agent can call /agent-runs/finish itself
  SHIP_WORKSPACE_ID    UUID of the workspace (a workspace is one project)
  GITHUB_REPOSITORY    owner/repo (which checkout the agent gets)
  CURSOR_API_KEY       Cursor agent CLI auth   (when bound provider=cursor)
  ANTHROPIC_API_KEY    Claude Code CLI auth     (when bound provider=claude)
  OPENAI_API_KEY       OpenAI Codex CLI auth    (when bound provider=codex)

  Provider is read from GET /v1/workspaces/{ws}/agent-provider.
  Falls back to 'cursor' if the API call fails so an unreachable
  Ship server doesn't strand the runner.

EXIT
  0  agent runtime reached a terminal state (FINISHED/ERRORED).
     Whether the agent actually called /agent-runs/finish is observable
     in the audit log — this CLI no longer waits on that signal.
  1  usage / config error
  7  agent runtime failed to launch
`);
}


function emit(args, payload) {
  if (args.json) {
    console.log(JSON.stringify(payload, null, 2));
  } else {
    console.error(`# ship: ${payload.status}${payload.reason ? ` reason=${payload.reason}` : ""}${payload.routine ? ` routine=${payload.routine}` : ""}`);
    if (payload.error) console.error(`#   error: ${payload.error}`);
  }
  // ``--status-file`` writes the same payload as a single JSON blob so a
  // workflow loop can branch on noop / ok without parsing stderr. The
  // schedule trigger loops through ``due_routines`` in priority order
  // and breaks on the first non-noop — without this file the loop would
  // need to grep stderr lines, which is fragile.
  if (args && args.statusFile) {
    try {
      fs.writeFileSync(args.statusFile, JSON.stringify(payload, null, 2));
    } catch (err) {
      console.error(`# ship: warn: could not write status file ${args.statusFile}: ${err && err.message ? err.message : err}`);
    }
  }
}


function die(code, msg) {
  console.error(msg);
  process.exit(code);
}
