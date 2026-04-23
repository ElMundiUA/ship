/**
 * `shipctl callback` — report the terminal status of a pipeline run back
 * to Ship.
 *
 * The customer's GitHub Actions workflow runs this in an `if: always()`
 * step at the end of the job. It replaces the 12-line `curl + HEREDOC`
 * boilerplate the previous starter workflows shipped with, so adopters
 * get a one-liner and a versioned CLI instead of hand-rolled JSON that
 * has silently broken every time Ship evolves the callback contract.
 *
 * URL resolution (first hit wins):
 *   1. `--callback-url <url>` flag
 *   2. `SHIP_CALLBACK_URL` env (what the existing workflow.yml injects)
 *   3. `--base-url <https://api.ship.example.com>` + `--run-id <uuid>`
 *      (or `SHIP_API_BASE` + `SHIP_RUN_ID` envs) → constructed as
 *      `{base}/v1/pipelines/runs/{run_id}/result`.
 *
 * Auth: exclusively the bearer token minted by Ship at dispatch time.
 *   - Required env: `SHIP_RUN_TOKEN`. We refuse to fall back to
 *     `SHIP_API_TOKEN` (the long-lived operator token used elsewhere in
 *     this CLI) because a workflow-context callback must *only* use the
 *     short-lived, run-scoped JWT. Cross-auth would silently hide bugs.
 *
 * This is intentionally **not** mounted under the `base-url` global flag
 * (which defaults to the public methodology host); run callbacks hit the
 * orchestration API (`api.ship.elmundi.com`), a different origin, so we
 * take the URL directly from the run context Ship injected.
 *
 * RFC-0010 Phase 3 (P3-03) extended this command to emit the new
 * `RunSummary` contract on top of the existing `status`/`summary`/
 * `metrics` body. Two equivalent code paths feed the same outcome:
 *
 *   - Per-field flags: `--outcome-text`, `--severity SEV=N`,
 *     `--artifact TYPE:TITLE[:REF]`, `--requires-approval`,
 *     `--approval-payload @file.json`, `--escalation TYPE:REASON`,
 *     `--findings-count N`. Composes well with bash heredoc / per-step
 *     authoring inside a workflow.
 *   - Bulk env input: `SHIP_RUN_OUTCOME` (inline JSON object) or
 *     `SHIP_RUN_OUTCOME_FILE` (path to JSON file). Suits agents that
 *     emit a full RunSummary blob on stdout.
 *
 * If both env and flags are present we MERGE — flags win on collision
 * (per-field shallow override at the top, per-key inside
 * `findings_by_severity`). Backwards compat is guaranteed: when none of
 * the new inputs are present, the request body is byte-identical to the
 * pre-P3 contract (no `outcome` key emitted).
 *
 * Outcome shape validation (severity vocabulary, escalation type enum
 * beyond what we accept on the CLI surface, etc.) is the BACKEND's job
 * (see RFC-0010 §RunSummary + the P3-01 Pydantic model). `shipctl` only
 * enforces well-formedness at the wire boundary: object-not-array,
 * field types, file readability, JSON parseability.
 */

import { readFileSync } from "node:fs";

/** @typedef {"succeeded"|"failed"|"cancelled"} TerminalStatus */

const STATUS_ALIASES = {
  ok: "succeeded",
  succeeded: "succeeded",
  success: "succeeded",
  pass: "succeeded",
  green: "succeeded",
  fail: "failed",
  failed: "failed",
  failure: "failed",
  red: "failed",
  cancelled: "cancelled",
  canceled: "cancelled",
  cancel: "cancelled",
};

/* RFC-0010 §RunSummary — the five inbox/escalation types Ship knows
 * how to route. We reject anything else at the CLI boundary so the
 * misspelled `--escalation aproval:...` fails fast with a usage hint
 * instead of being silently dropped by the backend's enum validator. */
const VALID_ESCALATION_TYPES = new Set([
  "clarification",
  "improvement",
  "failure",
  "approval",
  "exception",
]);

const VALID_SEVERITIES = new Set(["low", "medium", "high", "critical"]);

const OUTCOME_TEXT_MAX = 500;

const EXIT_USAGE = 2;
const EXIT_AUTH = 10;
const EXIT_CONFIG = 11;
const EXIT_HTTP = 3;

function die(code, msg) {
  console.error(msg);
  process.exit(code);
}

function printCallbackHelp() {
  console.log(`shipctl callback — report a pipeline run's terminal status to Ship.

USAGE
  shipctl callback --status <ok|fail|cancelled> [--summary "..."] [--metric k=v]...
                   [--outcome-text "..."] [--severity SEV=N]... [--artifact TYPE:TITLE[:REF]]...
                   [--requires-approval] [--approval-payload <@file|JSON>]
                   [--escalation TYPE:REASON]... [--findings-count N]

FLAGS
  --status       Terminal status. Aliases: ok|success|succeeded, fail|failed,
                 cancelled|canceled. Required.
  --summary      One-line human summary (≤1024 chars). Optional.
  --metric k=v   Structured metric to attach. Repeatable. Values coerced:
                 numbers, booleans (true|false), JSON (prefix { or [), else string.
                 Example: --metric tickets_processed=3 --metric dry_run=true
  --run-id       Pipeline run UUID (usually set by SHIP_RUN_ID env).
  --callback-url Full callback URL (usually set by SHIP_CALLBACK_URL env).
  --base-url     Orchestration API base (default: SHIP_API_BASE env). Combined
                 with --run-id to construct the URL when --callback-url absent.
  --json         Print the Ship response JSON on success.
  --help         Show this help.

OUTCOME FLAGS (RFC-0010 §RunSummary — emitted as the request body's "outcome" object)
  --outcome-text "..."     Pattern-authored single-line UI sentence (≤${OUTCOME_TEXT_MAX} chars,
                           leading/trailing whitespace trimmed). Example:
                             "3 issues found · 1 PR opened"
  --findings-count N       Non-negative integer total. If omitted but --severity
                           flags are present, derived from their sum.
  --severity SEV=N         Aggregated into outcome.findings_by_severity. SEV is one of
                           low|medium|high|critical. N is non-negative int. Repeatable;
                           order doesn't matter; last write wins per severity.
  --artifact TYPE:TITLE[:REF]
                           Repeatable. Parsed to {type, title, ref?}. Use \\: to embed
                           a literal colon inside TITLE. REF (URL or external id) is
                           optional. Example:
                             --artifact pr:"Fix null check":https://github.com/o/r/pull/42
  --requires-approval      Flag (no value). Sets outcome.requires_approval=true.
  --approval-payload PAYLOAD
                           JSON object to attach as outcome.approval_payload. Either
                           inline JSON or "@path/to/file.json" to load from disk.
                           Must parse to an object (not array/scalar).
  --escalation TYPE:REASON Repeatable. Aggregated into outcome.escalations[].
                           TYPE must be one of:
                             clarification | improvement | failure | approval | exception

ENV
  SHIP_RUN_TOKEN          (required) Short-lived bearer Ship issued for this run.
  SHIP_CALLBACK_URL       (preferred) Full URL of the result endpoint.
  SHIP_RUN_ID             Fallback input for --run-id.
  SHIP_API_BASE           Fallback input for --base-url.
  SHIP_RUN_OUTCOME        Inline JSON object — used as the base outcome (CLI flags
                          merge on top, flag values win on per-field collision).
  SHIP_RUN_OUTCOME_FILE   Path to a JSON file with the same semantics. Useful when
                          an agent emits a full RunSummary blob to stdout.

EXAMPLES
  # Existing — terminal status only:
  shipctl callback --status ok --summary "3 PRs scanned"

  # New — pattern-authored outcome:
  shipctl callback --status ok \\
    --outcome-text "3 issues found · 1 PR opened" \\
    --severity high=1 --severity medium=2 \\
    --artifact pr:"Fix null check":https://github.com/owner/repo/pull/42

  # New — bulk input from agent stdout:
  SHIP_RUN_OUTCOME_FILE=./summary.json shipctl callback --status ok
`);
}

/* Parse --metric k=v pairs with sensible coercion. We deliberately keep
 * this small — Ship's callback ``metrics`` blob is a free-form JSON bag,
 * so the CLI should offer the common shorthand (numbers, booleans, JSON
 * literals) without growing a tiny DSL. Strings are the fallback. */
function coerceMetricValue(raw) {
  if (raw === "") return "";
  if (raw === "true") return true;
  if (raw === "false") return false;
  if (raw === "null") return null;
  if (/^-?\d+$/.test(raw)) {
    const n = Number(raw);
    if (Number.isSafeInteger(n)) return n;
  }
  if (/^-?\d+\.\d+$/.test(raw)) {
    const n = Number(raw);
    if (Number.isFinite(n)) return n;
  }
  const first = raw[0];
  if (first === "{" || first === "[") {
    try {
      return JSON.parse(raw);
    } catch {
      /* fall through to string */
    }
  }
  return raw;
}

function parseMetricArg(tok) {
  const eq = tok.indexOf("=");
  if (eq <= 0) {
    die(EXIT_USAGE, `--metric expects key=value; got: ${tok}`);
  }
  const key = tok.slice(0, eq).trim();
  const value = tok.slice(eq + 1);
  if (!key) die(EXIT_USAGE, `--metric key cannot be empty: ${tok}`);
  return { key, value: coerceMetricValue(value) };
}

/* --severity SEV=N. Mirrors --metric's k=v shape but with a fixed
 * vocabulary and integer values. We validate at the CLI surface so the
 * `--severity hi=1` typo dies before we waste an HTTP round-trip. */
export function parseSeverityArg(tok) {
  const eq = tok.indexOf("=");
  if (eq <= 0) {
    die(EXIT_USAGE, `--severity expects SEV=N; got: ${tok}`);
  }
  const key = tok.slice(0, eq).trim().toLowerCase();
  const raw = tok.slice(eq + 1).trim();
  if (!VALID_SEVERITIES.has(key)) {
    die(
      EXIT_USAGE,
      `--severity SEV must be one of low|medium|high|critical; got: ${key}`,
    );
  }
  if (!/^\d+$/.test(raw)) {
    die(
      EXIT_USAGE,
      `--severity ${key}=N expects a non-negative integer; got: ${raw}`,
    );
  }
  const n = Number(raw);
  if (!Number.isSafeInteger(n) || n < 0) {
    die(
      EXIT_USAGE,
      `--severity ${key}=N expects a non-negative integer; got: ${raw}`,
    );
  }
  return { key, value: n };
}

/* --artifact TYPE:TITLE[:REF]. The annoying parser-y bit: TITLE may
 * embed colons via `\:`, REF is taken verbatim after the second
 * unescaped colon (so URLs like https://… survive the round-trip
 * without further escaping — only the post-TYPE post-TITLE colons
 * matter as separators). */
export function parseArtifactArg(tok) {
  const findUnescapedColon = (s, start = 0) => {
    for (let i = start; i < s.length; i++) {
      if (s[i] === ":" && s[i - 1] !== "\\") return i;
    }
    return -1;
  };
  const firstColon = findUnescapedColon(tok);
  if (firstColon <= 0) {
    die(EXIT_USAGE, `--artifact expects TYPE:TITLE[:REF]; got: ${tok}`);
  }
  const type = tok.slice(0, firstColon).trim();
  const rest = tok.slice(firstColon + 1);
  const secondColon = findUnescapedColon(rest);
  let titleRaw;
  let ref = null;
  if (secondColon < 0) {
    titleRaw = rest;
  } else {
    titleRaw = rest.slice(0, secondColon);
    ref = rest.slice(secondColon + 1);
  }
  /* Resolve the only escape we recognise. Other backslash sequences
   * pass through untouched — kept deliberately narrow so we don't grow
   * a DSL. */
  const title = titleRaw.replace(/\\:/g, ":").trim();
  if (!type) die(EXIT_USAGE, `--artifact TYPE cannot be empty: ${tok}`);
  if (!title) die(EXIT_USAGE, `--artifact TITLE cannot be empty: ${tok}`);
  /** @type {{type: string, title: string, ref?: string}} */
  const out = { type, title };
  if (ref !== null && ref !== "") out.ref = ref;
  return out;
}

/* --escalation TYPE:REASON. REASON is everything after the first
 * colon (so it can contain colons / URLs / punctuation freely). */
export function parseEscalationArg(tok) {
  const firstColon = tok.indexOf(":");
  if (firstColon <= 0) {
    die(EXIT_USAGE, `--escalation expects TYPE:REASON; got: ${tok}`);
  }
  const type = tok.slice(0, firstColon).trim().toLowerCase();
  const reason = tok.slice(firstColon + 1).trim();
  if (!VALID_ESCALATION_TYPES.has(type)) {
    die(
      EXIT_USAGE,
      `--escalation TYPE must be one of ${[...VALID_ESCALATION_TYPES].join("|")}; got: ${type}`,
    );
  }
  if (!reason) die(EXIT_USAGE, `--escalation REASON cannot be empty: ${tok}`);
  return { type, reason };
}

/* --approval-payload @path | inline JSON. Distinguishes by the leading
 * `@` because that's what bash/curl users already expect for
 * load-from-file semantics. We always require an object (not a top-level
 * array or scalar) so the backend's `Record<string, unknown>` slot lands
 * something usable. */
export function parseApprovalPayload(raw) {
  let source = raw;
  let origin = "inline JSON";
  if (typeof raw === "string" && raw.startsWith("@")) {
    const path = raw.slice(1);
    if (!path) die(EXIT_USAGE, "--approval-payload @ requires a file path");
    try {
      source = readFileSync(path, "utf8");
    } catch (err) {
      die(
        EXIT_CONFIG,
        `--approval-payload could not read ${path}: ${err instanceof Error ? err.message : err}`,
      );
    }
    origin = `file ${path}`;
  }
  let parsed;
  try {
    parsed = JSON.parse(source);
  } catch (err) {
    die(
      EXIT_CONFIG,
      `--approval-payload (${origin}) is not valid JSON: ${err instanceof Error ? err.message : err}`,
    );
    return null;
  }
  if (
    parsed === null ||
    typeof parsed !== "object" ||
    Array.isArray(parsed)
  ) {
    die(
      EXIT_CONFIG,
      `--approval-payload must be a JSON object (got ${Array.isArray(parsed) ? "array" : typeof parsed}).`,
    );
  }
  return parsed;
}

/* SHIP_RUN_OUTCOME / SHIP_RUN_OUTCOME_FILE → object. Returns null when
 * neither env is set. We do *not* validate the inner shape (severities,
 * escalation types, etc.) here — that's the backend's responsibility
 * per RFC-0010. We only enforce well-formedness so a typo in JSON
 * surfaces as an EXIT_CONFIG with a useful message rather than a 422
 * round-trip with the bearer already burned. */
export function loadEnvOutcome(env = process.env) {
  const inline = env.SHIP_RUN_OUTCOME;
  const file = env.SHIP_RUN_OUTCOME_FILE;
  if (!inline && !file) return null;
  let source;
  let origin;
  if (file) {
    try {
      source = readFileSync(file, "utf8");
    } catch (err) {
      die(
        EXIT_CONFIG,
        `SHIP_RUN_OUTCOME_FILE could not read ${file}: ${err instanceof Error ? err.message : err}`,
      );
      return null;
    }
    origin = `SHIP_RUN_OUTCOME_FILE=${file}`;
  } else {
    source = inline;
    origin = "SHIP_RUN_OUTCOME";
  }
  let parsed;
  try {
    parsed = JSON.parse(source);
  } catch (err) {
    die(
      EXIT_CONFIG,
      `${origin} is not valid JSON: ${err instanceof Error ? err.message : err}`,
    );
    return null;
  }
  if (
    parsed === null ||
    typeof parsed !== "object" ||
    Array.isArray(parsed)
  ) {
    die(
      EXIT_CONFIG,
      `${origin} must be a JSON object (got ${Array.isArray(parsed) ? "array" : typeof parsed}).`,
    );
  }
  return parsed;
}

export function parseCallbackArgs(rest) {
  const out = {
    status: null,
    summary: null,
    metrics: {},
    runId: null,
    callbackUrl: null,
    baseUrl: null,
    json: false,
    help: false,
    /* Outcome accumulator. We track presence of *any* outcome flag with
     * `outcomeFlagsSeen` so backwards-compat (no outcome key) is a
     * deterministic check rather than a "did everything end up empty"
     * heuristic. */
    outcome: {
      outcome_text: null,
      findings_count: null,
      findings_by_severity: {},
      artifacts: [],
      requires_approval: false,
      approval_payload: null,
      escalations: [],
    },
    outcomeFlagsSeen: false,
  };
  const copy = [...rest];
  const markOutcome = () => {
    out.outcomeFlagsSeen = true;
  };
  /* Tiny arg-munger kept inline rather than pulling a dependency —
   * matches the style of feedback.mjs / patterns.mjs and keeps this CLI
   * zero-prod-deps apart from `yaml`. */
  const strFlag = (name, key) => {
    if (copy[0] === name && copy[1] !== undefined) {
      copy.shift();
      out[key] = String(copy.shift());
      return true;
    }
    const p = `${name}=`;
    if (copy[0] && copy[0].startsWith(p)) {
      out[key] = copy[0].slice(p.length);
      copy.shift();
      return true;
    }
    return false;
  };
  /* Same as strFlag but routes the captured value into a callback so
   * we can validate / mutate (outcome flags don't live as plain keys
   * on `out`). */
  const handleArgFlag = (name, take) => {
    if (copy[0] === name && copy[1] !== undefined) {
      copy.shift();
      take(String(copy.shift()));
      return true;
    }
    const p = `${name}=`;
    if (copy[0] && copy[0].startsWith(p)) {
      const raw = copy[0].slice(p.length);
      copy.shift();
      take(raw);
      return true;
    }
    return false;
  };
  while (copy.length) {
    const a = copy[0];
    if (a === "--help" || a === "-h") {
      out.help = true;
      copy.shift();
      continue;
    }
    if (a === "--json") {
      out.json = true;
      copy.shift();
      continue;
    }
    if (strFlag("--status", "status")) continue;
    if (strFlag("--summary", "summary")) continue;
    if (strFlag("--run-id", "runId")) continue;
    if (strFlag("--callback-url", "callbackUrl")) continue;
    if (strFlag("--base-url", "baseUrl")) continue;
    if (a === "--metric" && copy[1] !== undefined) {
      copy.shift();
      const { key, value } = parseMetricArg(String(copy.shift()));
      out.metrics[key] = value;
      continue;
    }
    if (a && a.startsWith("--metric=")) {
      const raw = a.slice("--metric=".length);
      copy.shift();
      const { key, value } = parseMetricArg(raw);
      out.metrics[key] = value;
      continue;
    }
    if (
      handleArgFlag("--outcome-text", (raw) => {
        markOutcome();
        const trimmed = String(raw).trim();
        if (trimmed.length > OUTCOME_TEXT_MAX) {
          die(
            EXIT_USAGE,
            `--outcome-text exceeds ${OUTCOME_TEXT_MAX} chars (got ${trimmed.length}).`,
          );
        }
        out.outcome.outcome_text = trimmed;
      })
    )
      continue;
    if (
      handleArgFlag("--findings-count", (raw) => {
        markOutcome();
        const r = String(raw).trim();
        if (!/^\d+$/.test(r)) {
          die(
            EXIT_USAGE,
            `--findings-count expects a non-negative integer; got: ${r}`,
          );
        }
        out.outcome.findings_count = Number(r);
      })
    )
      continue;
    if (
      handleArgFlag("--severity", (raw) => {
        markOutcome();
        const { key, value } = parseSeverityArg(String(raw));
        out.outcome.findings_by_severity[key] = value;
      })
    )
      continue;
    if (
      handleArgFlag("--artifact", (raw) => {
        markOutcome();
        out.outcome.artifacts.push(parseArtifactArg(String(raw)));
      })
    )
      continue;
    if (a === "--requires-approval") {
      markOutcome();
      out.outcome.requires_approval = true;
      copy.shift();
      continue;
    }
    if (
      handleArgFlag("--approval-payload", (raw) => {
        markOutcome();
        out.outcome.approval_payload = parseApprovalPayload(String(raw));
      })
    )
      continue;
    if (
      handleArgFlag("--escalation", (raw) => {
        markOutcome();
        out.outcome.escalations.push(parseEscalationArg(String(raw)));
      })
    )
      continue;
    die(EXIT_USAGE, `unknown argument: ${a}\nRun: shipctl callback --help`);
  }
  return out;
}

export function normaliseStatus(raw) {
  if (!raw) return null;
  const lower = String(raw).toLowerCase().trim();
  return STATUS_ALIASES[lower] ?? null;
}

export function resolveCallbackUrl(args, env = process.env) {
  if (args.callbackUrl) return args.callbackUrl;
  if (env.SHIP_CALLBACK_URL) return env.SHIP_CALLBACK_URL;
  const runId = args.runId || env.SHIP_RUN_ID || null;
  const base = args.baseUrl || env.SHIP_API_BASE || null;
  if (runId && base) {
    return `${base.replace(/\/$/, "")}/v1/pipelines/runs/${runId}/result`;
  }
  return null;
}

/* Compose the final outcome from env (base) + CLI flags (overlay).
 *
 * Merge semantics — codified here so the test names stay terse and the
 * spec deviation (if any) is grep-able:
 *
 *   - Top-level keys: shallow override (CLI wins on collision).
 *   - `findings_by_severity`: per-key merge (env's `low: 1` survives a
 *     CLI `--severity high=2`; CLI overrides env when severities collide).
 *   - Arrays (`artifacts`, `escalations`): if CLI contributed any,
 *     CLI replaces env. Otherwise env's array passes through. We
 *     intentionally do NOT concat — appending env+CLI would be too
 *     surprising for an agent that emits a complete blob and then a
 *     human refines a single field via flag.
 *   - `findings_count`: when neither env nor flag provides one but
 *     `findings_by_severity` is present, we derive a sum so the
 *     pattern doesn't need to compute it by hand.
 */
export function buildOutcome(args, env = process.env) {
  const envOutcome = loadEnvOutcome(env);
  if (!envOutcome && !args.outcomeFlagsSeen) return null;

  /** @type {Record<string, unknown>} */
  const merged = envOutcome ? { ...envOutcome } : {};

  const cli = args.outcome;

  if (cli.outcome_text !== null) merged.outcome_text = cli.outcome_text;
  if (cli.findings_count !== null) merged.findings_count = cli.findings_count;

  const cliSeverities = cli.findings_by_severity;
  if (Object.keys(cliSeverities).length > 0) {
    const baseSev =
      merged.findings_by_severity &&
      typeof merged.findings_by_severity === "object" &&
      !Array.isArray(merged.findings_by_severity)
        ? { ...merged.findings_by_severity }
        : {};
    merged.findings_by_severity = { ...baseSev, ...cliSeverities };
  }

  if (cli.artifacts.length > 0) merged.artifacts = cli.artifacts;
  if (cli.requires_approval) merged.requires_approval = true;
  if (cli.approval_payload !== null)
    merged.approval_payload = cli.approval_payload;
  if (cli.escalations.length > 0) merged.escalations = cli.escalations;

  /* Derive `findings_count` from severity totals when neither side
   * specified one explicitly. Catches the common case where the
   * pattern emits per-severity counts and forgets the rollup. */
  if (
    merged.findings_count === undefined &&
    merged.findings_by_severity &&
    typeof merged.findings_by_severity === "object"
  ) {
    let sum = 0;
    let any = false;
    for (const v of Object.values(merged.findings_by_severity)) {
      if (typeof v === "number" && Number.isFinite(v)) {
        sum += v;
        any = true;
      }
    }
    if (any) merged.findings_count = sum;
  }

  return merged;
}

export function buildCallbackBody(args, env = process.env) {
  /** @type {Record<string, unknown>} */
  const body = { status: args.status };
  if (args.summary) body.summary = String(args.summary).slice(0, 1024);
  if (Object.keys(args.metrics).length > 0) body.metrics = args.metrics;
  const outcome = buildOutcome(args, env);
  if (outcome !== null) body.outcome = outcome;
  return body;
}

export async function callbackCommand(_ctx, rest) {
  const args = parseCallbackArgs(rest);
  if (args.help) {
    printCallbackHelp();
    return;
  }

  const status = normaliseStatus(args.status);
  if (!status) {
    die(
      EXIT_USAGE,
      `--status is required (ok|fail|cancelled). Got: ${args.status ?? "<missing>"}\nRun: shipctl callback --help`,
    );
  }
  args.status = status;

  const token = process.env.SHIP_RUN_TOKEN;
  if (!token) {
    die(
      EXIT_AUTH,
      "SHIP_RUN_TOKEN env var is required. Ship injects it into workflow_dispatch inputs; set it in the callback step's env block.",
    );
  }

  const url = resolveCallbackUrl(args);
  if (!url) {
    die(
      EXIT_CONFIG,
      "Cannot resolve callback URL. Set SHIP_CALLBACK_URL (preferred — Ship injects it), or pass --callback-url, or combine SHIP_API_BASE + SHIP_RUN_ID.",
    );
  }

  const body = buildCallbackBody(args);

  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        "User-Agent": await getUA(),
      },
      body: JSON.stringify(body),
    });
  } catch (err) {
    die(EXIT_HTTP, `callback POST failed: ${err instanceof Error ? err.message : err}`);
    return;
  }

  const text = await res.text();
  if (!res.ok) {
    const hint =
      res.status === 401
        ? " (check SHIP_RUN_TOKEN matches the run Ship dispatched)"
        : res.status === 404
          ? " (check SHIP_RUN_ID — the run may not exist)"
          : res.status === 422
            ? " (check --status is one of succeeded|failed|cancelled)"
            : "";
    die(
      EXIT_HTTP,
      `Ship rejected callback: HTTP ${res.status} ${res.statusText}${hint}\n${text}`,
    );
    return;
  }

  if (args.json) {
    console.log(text);
  } else {
    console.log(`callback accepted: ${status}${args.summary ? ` — ${args.summary}` : ""}`);
  }
}

/* Lazy import to keep the helper self-contained & testable. */
async function getUA() {
  try {
    const { getUserAgent } = await import("../version.mjs");
    return getUserAgent();
  } catch {
    return "shipctl-callback";
  }
}
