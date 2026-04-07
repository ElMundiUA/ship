/**
 * Workflow engine: transition rules, handoff logic, skip BA, blocked/escalation.
 */
import { READY_LABELS, STAGE_LABELS, RESULT_LABELS, } from "./agent-contracts.js";
export function computeHandoff(from, to, config) {
    const labelsToAdd = [];
    const labelsToRemove = [];
    labelsToRemove.push(READY_LABELS[from]);
    if (to) {
        labelsToAdd.push(STAGE_LABELS[to]);
        labelsToAdd.push(READY_LABELS[to]);
    }
    return {
        from,
        to,
        labelsToAdd,
        labelsToRemove,
        newState: to ? "Ready" : undefined,
    };
}
export function shouldSkipRole(role, labels, config) {
    for (const rule of config.workflow.skipRules) {
        if (rule.skipRole === role && labels.includes(rule.label)) {
            return true;
        }
    }
    return false;
}
export function isBugFlow(labels, config) {
    return labels.includes("flow:bug");
}
export function getEffectiveFirstRole(labels, config) {
    if (labels.includes("flow:bug"))
        return "bug-agent";
    if (shouldSkipRole("ba", labels, config))
        return "architect";
    return "ba";
}
export function canTakeIssue(role, labels, config) {
    const readyLabel = READY_LABELS[role];
    if (!labels.includes(readyLabel))
        return false;
    if (role === "ba" && (shouldSkipRole("ba", labels, config) || labels.includes("flow:bug"))) {
        return false;
    }
    if (role === "bug-agent" && !labels.includes("flow:bug"))
        return false;
    return true;
}
export function getBlockedHandoff(role, reason) {
    return {
        from: role,
        to: null,
        labelsToAdd: [RESULT_LABELS.blocked],
        labelsToRemove: [READY_LABELS[role]],
        newState: "Blocked",
    };
}
export function getEscalatedHandoff(role, reason) {
    return {
        from: role,
        to: null,
        labelsToAdd: [RESULT_LABELS.needsHuman],
        labelsToRemove: [READY_LABELS[role]],
        newState: "Blocked",
    };
}
export function getQaFailedHandoff() {
    return {
        from: "qa-automation",
        to: "developer",
        labelsToAdd: [STAGE_LABELS.developer, READY_LABELS.developer, RESULT_LABELS.failed],
        labelsToRemove: [READY_LABELS["qa-automation"]],
        newState: "Ready",
    };
}
//# sourceMappingURL=workflow-engine.js.map