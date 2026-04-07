/**
 * GitHub API client for PR creation.
 */

const GITHUB_API = "https://api.github.com";

/** HTML comment markers in PR bodies that indicate a Magic Container / preview deploy (comma-separated in env). */
function prPreviewMarkers(): string[] {
  const raw = (
    process.env.GITHUB_PR_PREVIEW_COMMENT_MARKERS ||
    process.env.GITHUB_PR_PREVIEW_COMMENT_MARKER ||
    ""
  ).trim();
  if (raw) {
    return raw.split(",").map((s) => s.trim()).filter(Boolean);
  }
  return ["<!-- ship-pr-preview -->"];
}

export interface CreatePROptions {
  token: string;
  owner: string;
  repo: string;
  head: string;
  base?: string;
  title: string;
  body?: string;
}

export async function createPR(opts: CreatePROptions): Promise<{ url: string; number: number } | null> {
  const res = await fetch(`${GITHUB_API}/repos/${opts.owner}/${opts.repo}/pulls`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${opts.token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title: opts.title,
      head: opts.head,
      base: opts.base ?? "main",
      body: opts.body ?? "",
    }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`GitHub API error ${res.status}: ${err}`);
  }

  const data = (await res.json()) as { html_url: string; number: number };
  return { url: data.html_url, number: data.number };
}

export async function getGitRemote(): Promise<{ owner: string; repo: string } | null> {
  const { execSync } = await import("node:child_process");
  try {
    const url = execSync("git config --get remote.origin.url", { encoding: "utf-8" }).trim();
    const m = url.match(/github\.com[:/]([^/]+)\/([^/]+?)(?:\.git)?$/);
    if (!m) return null;
    return { owner: m[1], repo: m[2].replace(/\.git$/, "") };
  } catch {
    return null;
  }
}

export async function getCurrentBranch(): Promise<string> {
  const { execSync } = await import("node:child_process");
  return execSync("git rev-parse --abbrev-ref HEAD", { encoding: "utf-8" }).trim();
}

export interface PRStatus {
  number: number;
  state: string;
  headSha: string;
  mergeable: boolean | null;
  hasPreviewDeploy: boolean;
  previewUrl?: string;
  checks: { name: string; conclusion: string | null; status: string }[];
  failedChecks: { name: string; conclusion: string; jobId?: number; htmlUrl?: string }[];
}

export async function getPRStatus(
  token: string,
  owner: string,
  repo: string,
  prNumber: number
): Promise<PRStatus> {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  const prRes = await fetch(`${GITHUB_API}/repos/${owner}/${repo}/pulls/${prNumber}`, { headers });
  if (!prRes.ok) throw new Error(`PR fetch failed: ${prRes.status}`);
  const pr = (await prRes.json()) as { head: { sha: string }; state: string; mergeable: boolean | null };

  const checksRes = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/commits/${pr.head.sha}/check-runs`,
    { headers }
  );
  const checksData =
    checksRes.ok ?
      ((await checksRes.json()) as { check_runs?: { name: string; conclusion: string | null; status: string }[] })
    : { check_runs: [] };
  let checkRuns = checksData.check_runs ?? [];

  // Fallback: Check Runs API can return empty for private repos; use Actions workflow runs
  const failedChecksWithJobId: { name: string; conclusion: string; jobId?: number; htmlUrl?: string }[] = [];
  if (checkRuns.length === 0) {
    const runsRes = await fetch(
      `${GITHUB_API}/repos/${owner}/${repo}/actions/runs?head_sha=${pr.head.sha}&per_page=10`,
      { headers }
    );
    if (runsRes.ok) {
      const runsData = (await runsRes.json()) as {
        workflow_runs?: { id: number; name: string; conclusion: string | null; status: string }[];
      };
      const runs = runsData.workflow_runs ?? [];
      for (const run of runs) {
        const jobRes = await fetch(`${GITHUB_API}/repos/${owner}/${repo}/actions/runs/${run.id}/jobs`, {
          headers,
        });
        if (!jobRes.ok) continue;
        const jobsData = (await jobRes.json()) as {
          jobs?: { id: number; name: string; conclusion: string | null; status: string; html_url?: string }[];
        };
        const jobs = jobsData.jobs ?? [];
        for (const job of jobs) {
          const entry = {
            name: `${run.name} / ${job.name}`,
            conclusion: job.conclusion,
            status: job.status,
          };
          checkRuns.push(entry);
          if (job.conclusion === "failure" || job.conclusion === "cancelled" || job.conclusion === "error") {
            failedChecksWithJobId.push({
              name: entry.name,
              conclusion: job.conclusion!,
              jobId: job.id,
              htmlUrl: job.html_url,
            });
          }
        }
        if (jobs.length === 0 && (run.conclusion === "failure" || run.conclusion === "cancelled" || run.conclusion === "error")) {
          checkRuns.push({ name: run.name, conclusion: run.conclusion, status: run.status });
        }
      }
    }
  }

  const commentsRes = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/issues/${prNumber}/comments`,
    { headers }
  );
  const comments = commentsRes.ok ? ((await commentsRes.json()) as { body?: string }[]) : [];
  const markers = prPreviewMarkers();
  const previewComment = comments.find(
    (c) => c.body && markers.some((m) => c.body!.includes(m))
  );
  let hasPreviewDeploy = false;
  let previewUrl = "";
  if (previewComment?.body) {
    if (previewComment.body.includes("✅ PR preview deployed")) hasPreviewDeploy = true;
    const urlMatch = previewComment.body.match(/Preview:\s*`?([^`\s]+)`?/);
    if (urlMatch && !urlMatch[1].includes("not detected")) previewUrl = urlMatch[1];
  }

  const checks = checkRuns.map((c) => ({
    name: c.name,
    conclusion: c.conclusion,
    status: c.status,
  }));
  const failedChecks =
    failedChecksWithJobId.length > 0
      ? failedChecksWithJobId
      : checks
          .filter(
            (c) => c.conclusion === "failure" || c.conclusion === "cancelled" || c.conclusion === "error"
          )
          .map((c) => ({ name: c.name, conclusion: c.conclusion! }));

  return {
    number: prNumber,
    state: pr.state,
    headSha: pr.head.sha,
    mergeable: pr.mergeable,
    hasPreviewDeploy,
    previewUrl: previewUrl || undefined,
    checks,
    failedChecks,
  };
}

/** Fetch job logs from GitHub Actions. Includes start (setup, Node version) + end (error) for full context. */
export async function getJobLogs(
  token: string,
  owner: string,
  repo: string,
  jobId: number,
  opts?: { headLines?: number; tailLines?: number; maxChars?: number }
): Promise<string> {
  const headLines = opts?.headLines ?? 80;
  const tailLines = opts?.tailLines ?? 150;
  const maxChars = opts?.maxChars ?? 25000;

  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  const res = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/actions/jobs/${jobId}/logs`,
    { headers, redirect: "follow" }
  );
  if (!res.ok) return "";
  const text = await res.text();
  const lines = text.split("\n");
  if (lines.length <= headLines + tailLines) return text.slice(0, maxChars);

  const head = lines.slice(0, headLines).join("\n");
  const tail = lines.slice(-tailLines).join("\n");
  const combined =
    "=== START (setup, Node version, env) ===\n\n" +
    head +
    "\n\n... (truncated) ...\n\n=== END (failure, error) ===\n\n" +
    tail;
  return combined.slice(0, maxChars);
}

/** Verify preview URL serves the real app, not Bunny "We're deploying" placeholder. */
export async function verifyPreviewLive(url: string): Promise<{ ok: boolean; reason?: string }> {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    const res = await fetch(url, {
      signal: controller.signal,
      redirect: "follow",
      headers: { "User-Agent": "linear-agent-release-check/1.0" },
    });
    clearTimeout(timeout);
    const html = await res.text();
    if (html.includes("We're deploying your app!") || html.includes("We're deploying your app")) {
      return { ok: false, reason: "Bunny still showing 'We're deploying' placeholder" };
    }
    if (res.status >= 500) return { ok: false, reason: `HTTP ${res.status}` };
    if (res.status >= 400) return { ok: false, reason: `HTTP ${res.status}` };
    return { ok: true };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return { ok: false, reason: msg.includes("abort") ? "timeout" : msg };
  }
}

export async function findPRByHead(
  token: string,
  owner: string,
  repo: string,
  head: string
): Promise<number | null> {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  for (const headParam of [`${owner}:${head}`, head]) {
    const res = await fetch(
      `${GITHUB_API}/repos/${owner}/${repo}/pulls?head=${encodeURIComponent(headParam)}&state=open`,
      { headers }
    );
    if (!res.ok) continue;
    const data = (await res.json()) as { number: number }[];
    if (data[0]) return data[0].number;
  }
  return null;
}
