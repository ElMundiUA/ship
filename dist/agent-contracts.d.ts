/**
 * Agent contracts: roles, labels, schemas for multi-role orchestration.
 */
import { z } from "zod";
export declare const ROLES: readonly ["ba", "bug-agent", "architect", "qa-architect", "developer", "qa-automation", "release-manager"];
export type Role = (typeof ROLES)[number];
export declare const ROLE_ORDER: Role[];
export declare function getNextRole(role: Role): Role | null;
export declare function getPrevRole(role: Role): Role | null;
export declare const LABEL_PREFIX: {
    readonly stage: "stage:";
    readonly ready: "ready:";
    readonly result: "result:";
    readonly flow: "flow:";
};
export declare const STAGE_LABELS: Record<Role, string>;
export declare const READY_LABELS: Record<Role, string>;
export declare const RESULT_LABELS: {
    readonly passed: "result:passed";
    readonly failed: "result:failed";
    readonly blocked: "result:blocked";
    readonly needsHuman: "result:needs-human";
    readonly skipped: "result:skipped";
};
export declare const FLOW_LABELS: {
    readonly noBa: "flow:no-ba";
    readonly bug: "flow:bug";
    readonly hotfix: "flow:hotfix";
    readonly manualMergeRequired: "flow:manual-merge-required";
    readonly previewRequired: "flow:preview-required";
    readonly releaseCandidate: "flow:release-candidate";
};
export declare const WORKFLOW_STATUSES: readonly ["Backlog", "Ready", "In Progress", "In Review", "Blocked", "Done", "Canceled"];
export type WorkflowStatus = (typeof WORKFLOW_STATUSES)[number];
export declare const AgentArtifactSchema: z.ZodObject<{
    agentRole: z.ZodEnum<["ba", "bug-agent", "architect", "qa-architect", "developer", "qa-automation", "release-manager"]>;
    agentRunId: z.ZodString;
    issue: z.ZodString;
    status: z.ZodEnum<["completed", "blocked", "failed", "escalated", "in_progress"]>;
    summary: z.ZodString;
    artifacts: z.ZodOptional<z.ZodArray<z.ZodString, "many">>;
    nextRole: z.ZodOptional<z.ZodNullable<z.ZodEnum<["ba", "bug-agent", "architect", "qa-architect", "developer", "qa-automation", "release-manager"]>>>;
    risks: z.ZodOptional<z.ZodArray<z.ZodString, "many">>;
    timestamp: z.ZodOptional<z.ZodString>;
}, "strip", z.ZodTypeAny, {
    agentRole: "ba" | "bug-agent" | "architect" | "qa-architect" | "developer" | "qa-automation" | "release-manager";
    status: "completed" | "blocked" | "failed" | "escalated" | "in_progress";
    agentRunId: string;
    issue: string;
    summary: string;
    artifacts?: string[] | undefined;
    nextRole?: "ba" | "bug-agent" | "architect" | "qa-architect" | "developer" | "qa-automation" | "release-manager" | null | undefined;
    risks?: string[] | undefined;
    timestamp?: string | undefined;
}, {
    agentRole: "ba" | "bug-agent" | "architect" | "qa-architect" | "developer" | "qa-automation" | "release-manager";
    status: "completed" | "blocked" | "failed" | "escalated" | "in_progress";
    agentRunId: string;
    issue: string;
    summary: string;
    artifacts?: string[] | undefined;
    nextRole?: "ba" | "bug-agent" | "architect" | "qa-architect" | "developer" | "qa-automation" | "release-manager" | null | undefined;
    risks?: string[] | undefined;
    timestamp?: string | undefined;
}>;
export type AgentArtifact = z.infer<typeof AgentArtifactSchema>;
export declare function formatAgentComment(artifact: AgentArtifact): string;
export declare function parseAgentComment(body: string): AgentArtifact | null;
//# sourceMappingURL=agent-contracts.d.ts.map