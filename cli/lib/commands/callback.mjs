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
 */

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

ENV
  SHIP_RUN_TOKEN     (required) Short-lived bearer Ship issued for this run.
  SHIP_CALLBACK_URL  (preferred) Full URL of the result endpoint.
  SHIP_RUN_ID        Fallback input for --run-id.
  SHIP_API_BASE      Fallback input for --base-url.

EXAMPLE (inside a workflow.yml ‹if: always()› step)
  shipctl callback --status ok \\
    --summary "Intake processed TICKET-42" \\
    --metric tickets_processed=1 \\
    --metric ticket_ids=LIN-42
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
  };
  const copy = [...rest];
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

export function buildCallbackBody(args) {
  /** @type {Record<string, unknown>} */
  const body = { status: args.status };
  if (args.summary) body.summary = String(args.summary).slice(0, 1024);
  if (Object.keys(args.metrics).length > 0) body.metrics = args.metrics;
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
