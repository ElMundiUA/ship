/**
 * GitHub API client for PR creation.
 */
export interface CreatePROptions {
    token: string;
    owner: string;
    repo: string;
    head: string;
    base?: string;
    title: string;
    body?: string;
}
export declare function createPR(opts: CreatePROptions): Promise<{
    url: string;
    number: number;
} | null>;
export declare function getGitRemote(): Promise<{
    owner: string;
    repo: string;
} | null>;
export declare function getCurrentBranch(): Promise<string>;
export interface PRStatus {
    number: number;
    state: string;
    headSha: string;
    mergeable: boolean | null;
    hasPreviewDeploy: boolean;
    previewUrl?: string;
    checks: {
        name: string;
        conclusion: string | null;
        status: string;
    }[];
    failedChecks: {
        name: string;
        conclusion: string;
        jobId?: number;
        htmlUrl?: string;
    }[];
}
export declare function getPRStatus(token: string, owner: string, repo: string, prNumber: number): Promise<PRStatus>;
/** Fetch job logs from GitHub Actions. Includes start (setup, Node version) + end (error) for full context. */
export declare function getJobLogs(token: string, owner: string, repo: string, jobId: number, opts?: {
    headLines?: number;
    tailLines?: number;
    maxChars?: number;
}): Promise<string>;
/** Verify preview URL serves the real app, not Bunny "We're deploying" placeholder. */
export declare function verifyPreviewLive(url: string): Promise<{
    ok: boolean;
    reason?: string;
}>;
/** Search for open PR with issue identifier (e.g. ELM-57) in title. */
export declare function findPRByIssueIdentifier(token: string, owner: string, repo: string, issueId: string): Promise<number | null>;
/** Fetch failed job logs from a workflow run (for CI failure self-heal). */
export declare function getFailedJobLogsFromRun(token: string, owner: string, repo: string, runId: number, opts?: {
    maxCharsPerJob?: number;
}): Promise<string>;
export declare function findPRByHead(token: string, owner: string, repo: string, head: string): Promise<number | null>;
//# sourceMappingURL=github-client.d.ts.map