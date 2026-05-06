import { execFileSync } from "node:child_process";
import path from "node:path";

/**
 * Build-time git log → changelog entries.
 *
 * The Ship workflow squash-merges PRs into `main` with subjects like:
 *
 *     navigator: consult_specialist subagent tool (PR3 of 3) (#155)
 *     fix: bump default Anthropic Haiku model to the post-preview stable id (#156)
 *     knowledge: extractor batch + Anthropic-preamble parse fix (#152)
 *
 * We pull the most recent commits from `main`, filter to those that look
 * like merged PRs (have a trailing `(#NNN)`), and extract a small,
 * stable shape that the changelog page renders. Commits without the
 * PR suffix (direct-to-main, very rare) are skipped — the page is the
 * "what shipped via the PR flow" log, not a raw `git log`.
 *
 * Runs once at build time. If git history isn't available (shallow
 * clone in CI without `fetch-depth: 0`, or a Docker layer without `.git`),
 * we return an empty list so the page renders with a soft-empty state
 * rather than failing the build.
 */

export type ChangelogEntry = {
  /** 7-char commit hash. */
  shortSha: string;
  /** ISO 8601 commit-author date, e.g. `2026-05-06T22:18:43+02:00`. */
  date: string;
  /** ISO date only, e.g. `2026-05-06`. Used for grouping. */
  day: string;
  /** Conventional-commit scope prefix, e.g. `navigator`, `fix`, `landing`. Empty when absent. */
  scope: string;
  /** Subject minus scope prefix and `(#NNN)` suffix. */
  title: string;
  /** PR number parsed from the trailing `(#NNN)`. */
  prNumber: number;
  /** Author name from `git log`. */
  author: string;
};

const REPO_ROOT = path.resolve(process.cwd(), "..");
const MAX_ENTRIES = 200;

// `` is ASCII unit separator — safe between fields, won't collide
// with anything humans put in commit messages.
const SEP = "";
const FORMAT = ["%h", "%aI", "%an", "%s"].join(SEP);

const PR_RE = /\s*\(#(\d+)\)\s*$/;
const SCOPE_RE = /^([a-z][a-z0-9_-]*(?:\([a-z0-9._-]+\))?):\s*/i;

function readGitLog(): string[] {
  try {
    const stdout = execFileSync(
      "git",
      [
        "log",
        `-n${MAX_ENTRIES}`,
        `--pretty=format:${FORMAT}`,
        "--no-merges",
        "main",
      ],
      { cwd: REPO_ROOT, encoding: "utf-8", maxBuffer: 8 * 1024 * 1024 },
    );
    return stdout.split("\n").filter(Boolean);
  } catch {
    // Shallow clone, no .git, or git not on PATH.
    return [];
  }
}

function parseLine(line: string): ChangelogEntry | null {
  const parts = line.split(SEP);
  if (parts.length < 4) return null;
  const [shortSha, date, author, subjectRaw] = parts;
  const prMatch = PR_RE.exec(subjectRaw);
  if (!prMatch) return null;
  const prNumber = Number(prMatch[1]);
  let subject = subjectRaw.replace(PR_RE, "");
  let scope = "";
  const scopeMatch = SCOPE_RE.exec(subject);
  if (scopeMatch) {
    scope = scopeMatch[1].toLowerCase();
    subject = subject.slice(scopeMatch[0].length);
  }
  const title = subject.trim();
  if (!title) return null;
  return {
    shortSha,
    date,
    day: date.slice(0, 10),
    scope,
    title,
    prNumber,
    author,
  };
}

export function listChangelogEntries(): ChangelogEntry[] {
  return readGitLog()
    .map(parseLine)
    .filter((entry): entry is ChangelogEntry => entry !== null);
}

// ---------------------------------------------------------------------------
// Week grouping
// ---------------------------------------------------------------------------

export type ChangelogGroup = {
  /** Group label, e.g. ``Navigator``, ``Fixes``, ``Other``. */
  label: string;
  entries: ChangelogEntry[];
};

export type ChangelogWeek = {
  /** Monday of the week, ISO date (yyyy-mm-dd). */
  weekStart: string;
  /** Sunday of the week, ISO date. */
  weekEnd: string;
  /** Human-friendly week range, e.g. ``May 5–11, 2026``. */
  rangeLabel: string;
  /** Entries grouped by scope-family. Order: product areas alpha, then
   *  Improvements, Fixes, Other at the bottom. */
  groups: ChangelogGroup[];
};

const FIXES_SCOPES = new Set(["fix", "fixes", "bug", "bugfix"]);
const IMPROVEMENTS_SCOPES = new Set(["chore", "refactor", "perf", "build", "ci", "test", "tests"]);

function startOfWeek(iso: string): Date {
  const d = new Date(`${iso}T00:00:00Z`);
  // 0 = Sun, 1 = Mon, … We want Monday as the start.
  const dow = d.getUTCDay();
  const offset = dow === 0 ? -6 : 1 - dow;
  d.setUTCDate(d.getUTCDate() + offset);
  return d;
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function formatWeekRange(start: Date, end: Date): string {
  const opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  const startStr = start.toLocaleDateString("en-US", { ...opts, timeZone: "UTC" });
  const endStr = end.toLocaleDateString("en-US", { ...opts, timeZone: "UTC" });
  const year = end.getUTCFullYear();
  if (start.getUTCMonth() === end.getUTCMonth()) {
    // "May 5–11, 2026"
    return `${startStr.split(" ")[0]} ${start.getUTCDate()}–${end.getUTCDate()}, ${year}`;
  }
  // "Apr 28 – May 4, 2026"
  return `${startStr} – ${endStr}, ${year}`;
}

function prettyScope(scope: string): string {
  if (!scope) return "Other";
  if (FIXES_SCOPES.has(scope)) return "Fixes";
  if (IMPROVEMENTS_SCOPES.has(scope)) return "Improvements";
  // Title-case the scope: "navigator" → "Navigator", "agent_runs" → "Agent runs",
  // "linear" → "Linear", "knowledge" → "Knowledge".
  const words = scope.replace(/[_-]/g, " ").split(/\s+/);
  return words
    .map((w, i) => (i === 0 ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

function compareGroupLabels(a: string, b: string): number {
  // Sort: product areas (alpha), then Improvements, Fixes, Other.
  const tail = ["Improvements", "Fixes", "Other"];
  const ai = tail.indexOf(a);
  const bi = tail.indexOf(b);
  if (ai === -1 && bi === -1) return a.localeCompare(b);
  if (ai === -1) return -1;
  if (bi === -1) return 1;
  return ai - bi;
}

export function listChangelogWeeks(): ChangelogWeek[] {
  const entries = listChangelogEntries();
  const buckets = new Map<string, ChangelogEntry[]>();
  for (const e of entries) {
    const start = isoDate(startOfWeek(e.day));
    if (!buckets.has(start)) buckets.set(start, []);
    buckets.get(start)!.push(e);
  }
  const weeks: ChangelogWeek[] = [];
  for (const [weekStart, weekEntries] of buckets) {
    const start = new Date(`${weekStart}T00:00:00Z`);
    const end = new Date(start);
    end.setUTCDate(end.getUTCDate() + 6);
    // Group by pretty scope.
    const groupMap = new Map<string, ChangelogEntry[]>();
    for (const e of weekEntries) {
      const label = prettyScope(e.scope);
      if (!groupMap.has(label)) groupMap.set(label, []);
      groupMap.get(label)!.push(e);
    }
    const groups: ChangelogGroup[] = Array.from(groupMap.entries())
      .map(([label, items]) => ({ label, entries: items }))
      .sort((a, b) => compareGroupLabels(a.label, b.label));
    weeks.push({
      weekStart,
      weekEnd: isoDate(end),
      rangeLabel: formatWeekRange(start, end),
      groups,
    });
  }
  weeks.sort((a, b) => (a.weekStart < b.weekStart ? 1 : -1));
  return weeks;
}
