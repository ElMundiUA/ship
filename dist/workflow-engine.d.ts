/**
 * Workflow engine: transition rules, handoff logic, skip BA, blocked/escalation.
 */
import type { Role } from "./agent-contracts.js";
import type { Config } from "./config.js";
export interface HandoffResult {
    from: Role;
    to: Role | null;
    labelsToAdd: string[];
    labelsToRemove: string[];
    newState?: string;
}
export declare function computeHandoff(from: Role, to: Role | null, config: Config): HandoffResult;
export declare function shouldSkipRole(role: Role, labels: string[], config: Config): boolean;
export declare function isBugFlow(labels: string[], config: Config): boolean;
export declare function getEffectiveFirstRole(labels: string[], config: Config): Role;
export declare function canTakeIssue(role: Role, labels: string[], config: Config): boolean;
export declare function getBlockedHandoff(role: Role, reason: string): HandoffResult;
export declare function getEscalatedHandoff(role: Role, reason: string): HandoffResult;
export declare function getQaFailedHandoff(): HandoffResult;
//# sourceMappingURL=workflow-engine.d.ts.map