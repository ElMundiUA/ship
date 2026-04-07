/**
 * Agent contracts: roles, labels, schemas for multi-role orchestration.
 */
import { z } from "zod";
// ─── Roles ─────────────────────────────────────────────────────────────────
export const ROLES = [
    "ba",
    "bug-agent",
    "architect",
    "qa-architect",
    "developer",
    "qa-automation",
    "release-manager",
];
// Feature flow: ba → architect → qa-architect → developer → release-manager → qa-automation
// Release Manager first: checks CI + deploy ready. QA then tests on live preview.
// Bug flow: bug-agent → developer (separate entry point)
export const ROLE_ORDER = [
    "ba",
    "bug-agent",
    "architect",
    "qa-architect",
    "developer",
    "release-manager",
    "qa-automation",
];
export function getNextRole(role) {
    if (role === "bug-agent")
        return "developer";
    const idx = ROLE_ORDER.indexOf(role);
    if (idx < 0 || idx >= ROLE_ORDER.length - 1)
        return null;
    let next = ROLE_ORDER[idx + 1];
    if (role === "ba" && next === "bug-agent")
        next = ROLE_ORDER[idx + 2];
    return next;
}
export function getPrevRole(role) {
    const idx = ROLE_ORDER.indexOf(role);
    if (idx <= 0)
        return null;
    let prev = ROLE_ORDER[idx - 1];
    if (role === "architect" && prev === "bug-agent")
        prev = ROLE_ORDER[idx - 2];
    return prev;
}
// ─── Label prefixes ────────────────────────────────────────────────────────
export const LABEL_PREFIX = {
    stage: "stage:",
    ready: "ready:",
    result: "result:",
    flow: "flow:",
};
export const STAGE_LABELS = {
    ba: "stage:ba",
    "bug-agent": "stage:bug-agent",
    architect: "stage:architect",
    "qa-architect": "stage:qa-architect",
    developer: "stage:developer",
    "qa-automation": "stage:qa-automation",
    "release-manager": "stage:release-manager",
};
export const READY_LABELS = {
    ba: "ready:ba",
    "bug-agent": "ready:bug-agent",
    architect: "ready:architect",
    "qa-architect": "ready:qa-architect",
    developer: "ready:developer",
    "qa-automation": "ready:qa-automation",
    "release-manager": "ready:release-manager",
};
export const RESULT_LABELS = {
    passed: "result:passed",
    failed: "result:failed",
    blocked: "result:blocked",
    needsHuman: "result:needs-human",
    skipped: "result:skipped",
};
export const FLOW_LABELS = {
    noBa: "flow:no-ba",
    bug: "flow:bug",
    hotfix: "flow:hotfix",
    manualMergeRequired: "flow:manual-merge-required",
    previewRequired: "flow:preview-required",
    releaseCandidate: "flow:release-candidate",
};
// ─── Workflow statuses (Linear) ─────────────────────────────────────────────
export const WORKFLOW_STATUSES = [
    "Backlog",
    "Ready",
    "In Progress",
    "In Review",
    "Blocked",
    "Done",
    "Canceled",
];
// ─── Agent artifact schema (machine-readable comment) ───────────────────────
export const AgentArtifactSchema = z.object({
    agentRole: z.enum(ROLES),
    agentRunId: z.string(),
    issue: z.string(),
    status: z.enum(["completed", "blocked", "failed", "escalated", "in_progress"]),
    summary: z.string(),
    artifacts: z.array(z.string()).optional(),
    nextRole: z.enum(ROLES).nullable().optional(),
    risks: z.array(z.string()).optional(),
    timestamp: z.string().datetime().optional(),
});
export function formatAgentComment(artifact) {
    return `\`\`\`json\n${JSON.stringify(artifact, null, 2)}\n\`\`\``;
}
export function parseAgentComment(body) {
    const match = body.match(/```json\s*([\s\S]*?)\s*```/);
    if (!match)
        return null;
    try {
        const parsed = JSON.parse(match[1]);
        return AgentArtifactSchema.parse(parsed);
    }
    catch {
        return null;
    }
}
//# sourceMappingURL=agent-contracts.js.map