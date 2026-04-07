/**
 * Linear API client via GraphQL.
 * Creates missing labels and workflow states automatically.
 */
import { GraphQLClient } from "graphql-request";
import type { Role } from "./agent-contracts.js";
import { type WorkflowStatus } from "./agent-contracts.js";
import type { Config } from "./config.js";
export interface LinearClientOptions {
    apiKey: string;
    teamId?: string;
}
export interface IssueFilters {
    role?: Role;
    withoutRole?: Role;
    status?: WorkflowStatus;
    labels?: string[];
}
export interface IssueSummary {
    id: string;
    identifier: string;
    title: string;
    description?: string;
    state: {
        name: string;
    };
    labels: {
        nodes: {
            name: string;
        }[];
    };
    assignee?: {
        name: string;
    };
    priority?: number;
    url?: string;
}
export interface Issue {
    id: string;
    identifier: string;
    title: string;
    description?: string | null;
    state?: {
        name: string;
    } | null;
    labels?: {
        nodes: {
            id: string;
            name: string;
        }[];
    } | null;
    assignee?: {
        id: string;
        name: string;
    } | null;
    priority?: number | null;
    url?: string | null;
}
export declare function createLinearClient(apiKey: string): GraphQLClient;
/** Resolve team ID for label creation (labels belong to a team in Linear). */
export declare function resolveTeamId(client: GraphQLClient, config: Config): Promise<string | null>;
export declare function getIssue(client: GraphQLClient, issueId: string): Promise<Issue | undefined>;
export declare function getIssueByIdentifier(client: GraphQLClient, identifier: string): Promise<Issue | undefined>;
export declare function listIssues(client: GraphQLClient, filters: IssueFilters, limit?: number): Promise<Issue[]>;
export declare function getNextIssueForRole(client: GraphQLClient, role: Role, withoutBa?: boolean): Promise<Issue | undefined>;
export declare function updateIssueState(client: GraphQLClient, issueId: string, stateName: string): Promise<boolean>;
export declare function addLabel(client: GraphQLClient, issueId: string, labelName: string, config?: Config): Promise<boolean>;
export declare function removeLabel(client: GraphQLClient, issueId: string, labelName: string): Promise<boolean>;
export declare function addComment(client: GraphQLClient, issueId: string, body: string): Promise<string | undefined>;
/**
 * Assignment: we use labels (stage:, ready:) and workflow status instead of
 * Linear assignee. No direct assign — agent "ownership" is implicit from labels.
 */
export declare function issueToSummary(issue: Issue): IssueSummary;
//# sourceMappingURL=linear-client.d.ts.map