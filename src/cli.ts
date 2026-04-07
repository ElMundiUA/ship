#!/usr/bin/env node
/**
 * linear-agent CLI - multi-role agent orchestration for Linear.
 */

import "dotenv/config";
import { program } from "commander";
import { loadConfig } from "./config.js";
import { createLinearClient, getIssueByIdentifier, getNextIssueForRole, listIssues, getIssue, updateIssueState, addLabel, removeLabel, addComment, issueToSummary } from "./linear-client.js";
import { createPR, getGitRemote, getCurrentBranch, getPRStatus, findPRByHead, getJobLogs, verifyPreviewLive } from "./github-client.js";
import { computeHandoff, getBlockedHandoff, getEscalatedHandoff } from "./workflow-engine.js";
import { formatAgentComment, getNextRole, type AgentArtifact, type Role, ROLES, STAGE_LABELS } from "./agent-contracts.js";

const config = loadConfig();
const apiKey = process.env[config.linear.apiKeyEnv];
const jsonOutput = process.argv.includes("--json");

function out(obj: unknown) {
  if (jsonOutput) {
    console.log(JSON.stringify(obj, null, 2));
  } else {
    if (typeof obj === "object" && obj !== null && "identifier" in obj) {
      const i = obj as { identifier: string; title?: string };
      console.log(`${i.identifier}: ${(i as { title?: string }).title ?? ""}`);
    } else {
      console.log(obj);
    }
  }
}


program
  .name("linear-agent")
  .description("CLI for multi-role agent orchestration with Linear")
  .option("--json", "Output as JSON")
  .option("--dry-run", "Do not persist changes");

// ─── Get commands ─────────────────────────────────────────────────────────

program
  .command("next")
  .description("Get the next available issue for a role")
  .requiredOption("-r, --role <role>", `Role: ${ROLES.join(", ")}`)
  .option("--without-ba", "Include issues that skip BA stage")
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const client = createLinearClient(apiKey);
    const issue = await getNextIssueForRole(client, opts.role as Role, opts.withoutBa);
    if (!issue) {
      out({ ok: false, message: "No issues available" });
      process.exit(1);
    }
    out(issueToSummary(issue));
  });

program
  .command("list")
  .description("List all issues for a role (or without a role)")
  .option("-r, --role <role>", `Filter by role: ${ROLES.join(", ")}`)
  .option("--without-role <role>", "Filter: without role (e.g. ba for flow:no-ba)")
  .option("-l, --limit <n>", "Max issues", "50")
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const client = createLinearClient(apiKey);
    const filters: { role?: Role; withoutRole?: Role } = {};
    if (opts.role) filters.role = opts.role as Role;
    if (opts.withoutRole) filters.withoutRole = opts.withoutRole as Role;
    const issues = await listIssues(client, filters, parseInt(opts.limit, 10));
    out(issues.map(issueToSummary));
  });

program
  .command("init")
  .description("Initialize issue for pipeline (add stage:ba + ready:ba, or ready:architect if flow:no-ba)")
  .requiredOption("-i, --issue <id>", "Issue ID or identifier")
  .option("--no-ba", "Skip BA stage (add flow:no-ba, ready:architect)")
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const client = createLinearClient(apiKey);
    const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }
    if (dryRun) {
      out({ dryRun: true, wouldAdd: opts.noBa ? ["flow:no-ba", "ready:architect", "stage:architect"] : ["stage:ba", "ready:ba"] });
      return;
    }
    if (opts.noBa) {
      await addLabel(client, issue.id, "flow:no-ba", config);
      await addLabel(client, issue.id, "stage:architect", config);
      await addLabel(client, issue.id, "ready:architect", config);
    } else {
      await addLabel(client, issue.id, "stage:ba", config);
      await addLabel(client, issue.id, "ready:ba", config);
    }
    out({ ok: true, issue: issue.identifier });
  });

program
  .command("get")
  .description("Get issue details by ID or identifier (e.g. ENG-123)")
  .argument("<issue>", "Issue ID or identifier")
  .action(async (issueArg) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const client = createLinearClient(apiKey);
    const issueId = issueArg.match(/^[a-f0-9-]{36}$/i)
      ? issueArg
      : (await getIssueByIdentifier(client, issueArg))?.id;
    if (!issueId) {
      console.error("Issue not found");
      process.exit(1);
    }
    const issue = await getIssue(client, issueId);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }
    out(issueToSummary(issue));
  });

// ─── Lifecycle commands ───────────────────────────────────────────────────

program
  .command("start")
  .description("Start work on an issue (set In Progress, add stage label)")
  .requiredOption("-i, --issue <id>", "Issue ID or identifier")
  .requiredOption("-r, --role <role>", `Role: ${ROLES.join(", ")}`)
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const client = createLinearClient(apiKey);
    const issueId = opts.issue.match(/^[a-f0-9-]{36}$/i)
      ? opts.issue
      : (await getIssueByIdentifier(client, opts.issue))?.id;
    if (!issueId) {
      console.error("Issue not found");
      process.exit(1);
    }
    if (dryRun) {
      out({ dryRun: true, wouldUpdate: { state: "In Progress", addLabel: STAGE_LABELS[opts.role as Role] } });
      return;
    }
    const moved = await updateIssueState(client, issueId, "In Progress");
    if (!moved) {
      console.error("Failed to move issue to In Progress (check team workflow state names in Linear)");
      process.exit(1);
    }
    const labeled = await addLabel(client, issueId, STAGE_LABELS[opts.role as Role], config);
    if (!labeled) {
      console.error("Failed to add stage label");
      process.exit(1);
    }
    out({ ok: true, issue: issueId });
  });

program
  .command("complete")
  .description("Complete role work and hand off to next role")
  .requiredOption("-i, --issue <id>", "Issue ID or identifier")
  .requiredOption("-r, --role <role>", `Role: ${ROLES.join(", ")}`)
  .option("-s, --summary <text>", "Summary of work done")
  .option("-a, --artifacts <paths>", "Comma-separated artifact paths")
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const client = createLinearClient(apiKey);
    const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }
    const role = opts.role as Role;
    const nextRole = getNextRole(role);
    const handoff = computeHandoff(role, nextRole, config);

    if (dryRun) {
      out({ dryRun: true, handoff });
      return;
    }

    for (const lbl of handoff.labelsToRemove) {
      await removeLabel(client, issue.id, lbl);
    }
    for (const lbl of handoff.labelsToAdd) {
      await addLabel(client, issue.id, lbl, config);
    }
    if (handoff.newState) {
      await updateIssueState(client, issue.id, handoff.newState);
    }

    const artifact: AgentArtifact = {
      agentRole: role,
      agentRunId: `run_${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}`,
      issue: issue.identifier,
      status: "completed",
      summary: opts.summary ?? "Work completed",
      artifacts: opts.artifacts?.split(",").map((s: string) => s.trim()) ?? [],
      nextRole: handoff.to,
      timestamp: new Date().toISOString(),
    };
    await addComment(client, issue.id, formatAgentComment(artifact));
    out({ ok: true, handoff: handoff.to });
  });

program
  .command("handoff")
  .description("Hand off issue from one role to another")
  .requiredOption("-i, --issue <id>", "Issue ID or identifier")
  .requiredOption("--from <role>", `From role: ${ROLES.join(", ")}`)
  .requiredOption("--to <role>", `To role: ${ROLES.join(", ")}`)
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const client = createLinearClient(apiKey);
    const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }
    const handoff = computeHandoff(opts.from as Role, opts.to as Role, config);
    if (dryRun) {
      out({ dryRun: true, handoff });
      return;
    }
    for (const lbl of handoff.labelsToRemove) {
      await removeLabel(client, issue.id, lbl);
    }
    for (const lbl of handoff.labelsToAdd) {
      await addLabel(client, issue.id, lbl, config);
    }
    if (handoff.newState) {
      await updateIssueState(client, issue.id, handoff.newState);
    }
    out({ ok: true, handoff: handoff.to });
  });

program
  .command("block")
  .description("Block an issue")
  .requiredOption("-i, --issue <id>", "Issue ID or identifier")
  .requiredOption("-r, --role <role>", `Role: ${ROLES.join(", ")}`)
  .requiredOption("--reason <text>", "Reason for blocking")
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const client = createLinearClient(apiKey);
    const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }
    const handoff = getBlockedHandoff(opts.role as Role, opts.reason);
    if (dryRun) {
      out({ dryRun: true, handoff });
      return;
    }
    for (const lbl of handoff.labelsToRemove) {
      await removeLabel(client, issue.id, lbl);
    }
    for (const lbl of handoff.labelsToAdd) {
      await addLabel(client, issue.id, lbl, config);
    }
    await updateIssueState(client, issue.id, "Blocked");
    await addComment(client, issue.id, `**Blocked** (${opts.role}): ${opts.reason}`);
    out({ ok: true });
  });

program
  .command("escalate")
  .description("Escalate issue to human")
  .requiredOption("-i, --issue <id>", "Issue ID or identifier")
  .requiredOption("-r, --role <role>", `Role: ${ROLES.join(", ")}`)
  .requiredOption("--reason <text>", "Reason for escalation")
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const client = createLinearClient(apiKey);
    const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }
    const handoff = getEscalatedHandoff(opts.role as Role, opts.reason);
    if (dryRun) {
      out({ dryRun: true, handoff });
      return;
    }
    for (const lbl of handoff.labelsToRemove) {
      await removeLabel(client, issue.id, lbl);
    }
    for (const lbl of handoff.labelsToAdd) {
      await addLabel(client, issue.id, lbl, config);
    }
    await updateIssueState(client, issue.id, "Blocked");
    await addComment(client, issue.id, `**Escalated** (${opts.role}): ${opts.reason}`);
    out({ ok: true });
  });

// ─── Label / metadata ──────────────────────────────────────────────────────

program
  .command("label-add")
  .description("Add label to issue")
  .requiredOption("-i, --issue <id>", "Issue ID or identifier")
  .requiredOption("-l, --label <name>", "Label name")
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const client = createLinearClient(apiKey);
    const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }
    if (dryRun) {
      out({ dryRun: true, wouldAdd: opts.label });
      return;
    }
    await addLabel(client, issue.id, opts.label, config);
    out({ ok: true });
  });

program
  .command("label-remove")
  .description("Remove label from issue")
  .requiredOption("-i, --issue <id>", "Issue ID or identifier")
  .requiredOption("-l, --label <name>", "Label name")
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const client = createLinearClient(apiKey);
    const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }
    if (dryRun) {
      out({ dryRun: true, wouldRemove: opts.label });
      return;
    }
    await removeLabel(client, issue.id, opts.label);
    out({ ok: true });
  });

program
  .command("status-set")
  .description("Set workflow status")
  .requiredOption("-i, --issue <id>", "Issue ID or identifier")
  .requiredOption("-s, --status <name>", "Status: Backlog, Ready, In Progress, In Review, Blocked, Done, Canceled")
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const client = createLinearClient(apiKey);
    const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }
    if (dryRun) {
      out({ dryRun: true, wouldSet: opts.status });
      return;
    }
    await updateIssueState(client, issue.id, opts.status);
    out({ ok: true });
  });

program
  .command("comment")
  .description("Add comment to issue (optionally from file)")
  .requiredOption("-i, --issue <id>", "Issue ID or identifier")
  .option("-t, --text <text>", "Comment text")
  .option("-f, --file <path>", "Read comment from file")
  .action(async (opts) => {
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const client = createLinearClient(apiKey);
    const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }
    let body = opts.text ?? "";
    if (opts.file) {
      const fs = await import("node:fs");
      body = fs.readFileSync(opts.file, "utf-8");
    }
    if (!body) {
      console.error("Provide --text or --file");
      process.exit(1);
    }
    if (dryRun) {
      out({ dryRun: true, wouldComment: body.slice(0, 100) + "..." });
      return;
    }
    const commentId = await addComment(client, issue.id, body);
    out({ ok: true, commentId });
  });

program
  .command("pr-create")
  .description("Create PR from current branch, optionally linked to Linear issue")
  .requiredOption("-i, --issue <id>", "Linear issue identifier (e.g. ELM-62)")
  .option("-b, --base <branch>", "Base branch", "main")
  .option("--head <branch>", "Head branch (default: current git branch)")
  .option("-t, --title <text>", "PR title (default: fix(ISSUE): from Linear)")
  .action(async (opts) => {
    const token = process.env.GITHUB_TOKEN;
    if (!token) {
      console.error("Missing GITHUB_TOKEN");
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const remote = await getGitRemote();
    if (!remote) {
      console.error("Could not detect git remote (origin)");
      process.exit(1);
    }
    const head = opts.head ?? (await getCurrentBranch());
    let title = opts.title;
    let body = "";

    if (apiKey) {
      const client = createLinearClient(apiKey);
      const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
      if (issue) {
        if (!title) title = `fix(${opts.issue}): ${issue.title}`;
        body = `Closes ${opts.issue}\n\n${issue.description ?? ""}`;
      }
    }
    if (!title) title = `fix(${opts.issue})`;

    if (dryRun) {
      out({ dryRun: true, wouldCreate: { owner: remote.owner, repo: remote.repo, head, base: opts.base, title } });
      return;
    }

    try {
      const existing = await findPRByHead(token, remote.owner, remote.repo, head);
      if (existing) {
        out({ ok: true, url: `https://github.com/${remote.owner}/${remote.repo}/pull/${existing}`, number: existing, existing: true });
        return;
      }

      const pr = await createPR({
        token,
        owner: remote.owner,
        repo: remote.repo,
        head,
        base: opts.base,
        title,
        body,
      });
      out({ ok: true, url: pr!.url, number: pr!.number });
    } catch (e) {
      console.error("Failed to create PR:", e);
      process.exit(1);
    }
  });

program
  .command("release-check")
  .description("Release Manager: check PR status, deploy, move to In Review or return to Developer")
  .requiredOption("-i, --issue <id>", "Linear issue identifier")
  .option("--pr <number>", "PR number (default: find by current branch)")
  .option("--force-in-review", "Move to In Review even without deploy (override)")
  .action(async (opts) => {
    const token = process.env.GITHUB_TOKEN;
    if (!token) {
      console.error("Missing GITHUB_TOKEN");
      process.exit(1);
    }
    if (!apiKey) {
      console.error(`Missing ${config.linear.apiKeyEnv}`);
      process.exit(1);
    }
    const dryRun = program.opts().dryRun;
    const remote = await getGitRemote();
    if (!remote) {
      console.error("Could not detect git remote");
      process.exit(1);
    }

    let prNumber = opts.pr ? parseInt(opts.pr, 10) : null;
    if (!prNumber) {
      const branch = await getCurrentBranch();
      prNumber = await findPRByHead(token, remote.owner, remote.repo, branch);
    }
    if (!prNumber) {
      console.error("Could not find PR. Use --pr <number> or run from PR branch.");
      process.exit(1);
    }

    const status = await getPRStatus(token, remote.owner, remote.repo, prNumber);
    const client = createLinearClient(apiKey);
    const issue = await getIssueByIdentifier(client, opts.issue) ?? await getIssue(client, opts.issue);
    if (!issue) {
      console.error("Issue not found");
      process.exit(1);
    }

    const failurePayload = {
      prNumber: status.number,
      failedChecks: status.failedChecks,
      hasPreviewDeploy: status.hasPreviewDeploy,
      previewUrl: status.previewUrl,
    };

    if (status.failedChecks.length > 0) {
      let failureText = `**CI failed** (Release Manager check)\n\nPR: https://github.com/${remote.owner}/${remote.repo}/pull/${status.number}\n\nFailed checks:\n${status.failedChecks.map((c) => `- ${c.name}: ${c.conclusion}${c.htmlUrl ? ` — [Full log in GitHub](${c.htmlUrl})` : ""}`).join("\n")}`;
      for (const fc of status.failedChecks) {
        if (fc.jobId) {
          const logs = await getJobLogs(token, remote.owner, remote.repo, fc.jobId);
          if (logs) {
            const escaped = logs.replace(/```/g, "` ` `").slice(0, 20000);
            failureText += `\n\n<details><summary>Log: ${fc.name}</summary>\n\n\`\`\`\n${escaped}\n\`\`\`\n</details>`;
          }
        }
      }
      failureText += `\n\nReturning to Developer for fix.`;
      if (!dryRun) {
        await addComment(client, issue.id, failureText);
        const handoff = computeHandoff("release-manager", "developer", config);
        for (const lbl of handoff.labelsToRemove) await removeLabel(client, issue.id, lbl);
        for (const lbl of handoff.labelsToAdd) await addLabel(client, issue.id, lbl, config);
        await addLabel(client, issue.id, "result:failed", config);
        await updateIssueState(client, issue.id, "Ready");
      }
      out({ ok: false, action: "returned_to_developer", reason: "ci_failed", ...failurePayload });
      process.exit(1);
    }

    if (!status.hasPreviewDeploy && !opts.forceInReview) {
      const msg = `**Waiting for deploy** (Release Manager)\n\nPR: https://github.com/${remote.owner}/${remote.repo}/pull/${status.number}\n\nPreview deploy not ready yet. Not moving to In Review until deploy exists.\nUse \`--force-in-review\` to override.`;
      if (!dryRun) await addComment(client, issue.id, msg);
      out({ ok: false, action: "waiting_for_deploy", ...failurePayload });
      process.exit(1);
    }

    if (status.previewUrl && !opts.forceInReview) {
      const verify = await verifyPreviewLive(status.previewUrl);
      if (!verify.ok) {
        const msg = `**Preview not live yet** (Release Manager)\n\nPR: https://github.com/${remote.owner}/${remote.repo}/pull/${status.number}\nPreview: ${status.previewUrl}\n\nReason: ${verify.reason || "unknown"}\n\nBunny may still be deploying. Try again in a few minutes.`;
        if (!dryRun) await addComment(client, issue.id, msg);
        out({ ok: false, action: "waiting_for_deploy", previewNotLive: true, reason: verify.reason, ...failurePayload });
        process.exit(1);
      }
    }

    if (!dryRun) {
      await updateIssueState(client, issue.id, "In Review");
      await addComment(
        client,
        issue.id,
        `**Ready for human review**\n\nPR: https://github.com/${remote.owner}/${remote.repo}/pull/${status.number}\nPreview: ${status.previewUrl || "(see PR comment)"}\n\nMerge remains human-only.`
      );
    }
    out({ ok: true, action: "moved_to_in_review", prNumber: status.number, previewUrl: status.previewUrl });
  });

program.parse();
