import fs from "node:fs";
import path from "node:path";

/**
 * Bootstrap renderer for `shipctl init --bootstrap`.
 *
 * This is intentionally a v1: full preset-body interpretation (parsing
 * `## Bootstrap (files to write)` blocks from adapter artifacts) is TODO
 * and tracked as a templating engine in RFC-0004. For now we:
 *
 *   - Always emit a SHIP_BOOTSTRAP_PLAN.md summary so the user has a
 *     single actionable next-step document.
 *   - For the common `mobile-app + gh-actions + linear` triple we also
 *     write minimal CI workflow skeleton, label contract YAML, and
 *     `.env.example` placeholders. Other combos fall back to plan-only.
 *
 * @typedef {Object} PlanFile
 * @property {string} path     Relative to cwd.
 * @property {string} content
 * @property {"create"|"append"|"patch"} mode
 *
 * @typedef {Object} PlanSummary
 * @property {string[]} notes
 * @property {Array<{path:string, mode:string, detail?:string}>} files
 *
 * @typedef {Object} RenderedPlan
 * @property {PlanFile[]} files
 * @property {PlanSummary} summary
 */

const MOBILE_LABELS = [
  "platform:ios",
  "platform:android",
  "store:review",
  "flag:behind",
  "flag:ahead",
  "change-record",
  "blocked",
  "preview:ready",
];

const ENV_EXAMPLE_MARKER_START = "# --- ship-managed ---";
const ENV_EXAMPLE_MARKER_END = "# --- end ship-managed ---";

/**
 * @param {object} cfg
 * @returns {RenderedPlan}
 */
export function renderMobileAppGhActionsLinear(cfg) {
  const preset = cfg.stack?.preset || "mobile-app";
  const tracker = cfg.stack?.tracker || "linear";
  const ci = cfg.stack?.ci || "gh-actions";
  const agents = Array.isArray(cfg.stack?.agents) ? cfg.stack.agents : [];

  const workflow = `# ship-managed: workflow
# Skeleton written by \`shipctl init --bootstrap\`.
# shipctl sync (Epic 7) will fill in job bodies from preset:preset-${preset}.
name: ship-pilot
on:
  pull_request:
  push:
    branches: [main]

jobs:
  # ship-managed: workflow
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # TODO: language-specific lint wired by shipctl sync
      - run: echo "lint: placeholder"

  # ship-managed: workflow
  build-ios:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      # TODO: EAS / Fastlane build steps wired by shipctl sync
      - run: echo "build-ios: placeholder"

  # ship-managed: workflow
  build-android:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # TODO: Gradle / EAS build steps wired by shipctl sync
      - run: echo "build-android: placeholder"
`;

  const labelsYml = `# ship-managed: labels
# Synced to the tracker (${tracker}) by \`shipctl verify\`.
version: 1
preset: ${preset}
labels:
${MOBILE_LABELS.map((l) => `  - name: "${l}"`).join("\n")}
`;

  const envBlock = `${ENV_EXAMPLE_MARKER_START}
# Placeholders for ${preset} / ${tracker} / ${ci}.
# Fill these in .env.local (not committed) or your platform secret store.
LINEAR_API_KEY=
LINEAR_TEAM_ID=
GITHUB_TOKEN=
EXPO_TOKEN=
SENTRY_AUTH_TOKEN=
${ENV_EXAMPLE_MARKER_END}
`;

  const plan = renderAdoptionMinimum(cfg, {
    extraNotes: [
      "Mobile-app pilot scaffolding was emitted (gh-actions + linear).",
      "See `.github/workflows/ship-pilot.yml`, `.ship/labels.yml`, `.env.example`.",
    ],
  });

  const files = [
    ...plan.files,
    {
      path: ".github/workflows/ship-pilot.yml",
      content: workflow,
      mode: /** @type {"create"} */ ("create"),
    },
    {
      path: ".ship/labels.yml",
      content: labelsYml,
      mode: /** @type {"create"} */ ("create"),
    },
    {
      path: ".env.example",
      content: envBlock,
      mode: /** @type {"append"} */ ("append"),
    },
  ];

  const summary = {
    notes: [
      ...plan.summary.notes,
      `bootstrap: mobile-app + ${ci} + ${tracker} triple rendered`,
      `agents: ${agents.join(", ") || "(none)"}`,
    ],
    files: files.map((f) => ({
      path: f.path,
      mode: f.mode,
      detail:
        f.path === ".ship/labels.yml"
          ? `${MOBILE_LABELS.length} labels`
          : f.path === ".env.example"
            ? "5 placeholders"
            : undefined,
    })),
  };

  return { files, summary };
}

/**
 * Generate just the SHIP_BOOTSTRAP_PLAN.md summary. Used as the v1
 * fallback for preset / CI / tracker combos that don't have a specific
 * renderer yet.
 *
 * @param {object} cfg
 * @param {{extraNotes?:string[]}} [opts]
 * @returns {RenderedPlan}
 */
export function renderAdoptionMinimum(cfg, opts = {}) {
  const stack = cfg.stack || {};
  const preset = stack.preset || "adoption-minimum";
  const tracker = stack.tracker || "none";
  const ci = stack.ci || "manual";
  const agents = Array.isArray(stack.agents) ? stack.agents : [];
  const language = stack.language || "multi";
  const channel = cfg.api?.channel || "stable";
  const telemetry = cfg.telemetry?.share === true ? "on" : "off";
  const extraNotes = opts.extraNotes || [];

  const todos = buildTodoList({ preset, ci, tracker, agents });
  const recommendedTools = buildRecommendedTools({ preset });
  const recommendedSecrets = buildRecommendedSecrets({ tracker, ci });

  const body = `# Ship bootstrap plan

_Generated by \`shipctl init --bootstrap\` on ${new Date().toISOString()}._

## Chosen stack

- **preset**: \`${preset}\`
- **tracker**: \`${tracker}\`
- **ci**: \`${ci}\`
- **language**: \`${language}\`
- **agents**: ${agents.length ? agents.map((a) => `\`${a}\``).join(", ") : "_(none)_"}
- **channel**: \`${channel}\`
- **telemetry**: \`${telemetry}\`

## Recommended tools

${recommendedTools.map((t) => `- ${t}`).join("\n") || "_(none for this preset yet — fill manually.)_"}

## Recommended secrets / env

${recommendedSecrets.map((s) => `- \`${s}\``).join("\n") || "_(none required.)_"}

## Files to create / review

${todos.map((t) => `- [ ] ${t}`).join("\n")}

## Next steps

1. \`shipctl sync\` to refresh \`.ship/cache/\` against the Ship API.
2. \`shipctl verify\` to confirm tracker labels / CI secrets / rules markers.
3. Open the preset artifact for full details:
   \`shipctl collection show preset-${preset}\`.

${
  extraNotes.length
    ? `## Notes\n\n${extraNotes.map((n) => `- ${n}`).join("\n")}\n`
    : ""
}`;

  return {
    files: [
      {
        path: "SHIP_BOOTSTRAP_PLAN.md",
        content: body,
        mode: /** @type {"create"} */ ("create"),
      },
    ],
    summary: {
      notes: ["bootstrap: plan-only fallback rendered (SHIP_BOOTSTRAP_PLAN.md)"],
      files: [
        {
          path: "SHIP_BOOTSTRAP_PLAN.md",
          mode: "create",
          detail: `${todos.length} todo items`,
        },
      ],
    },
  };
}

function buildTodoList({ preset, ci, tracker, agents }) {
  const todos = [];
  if (ci === "gh-actions") {
    todos.push("Confirm `.github/workflows/ship-pilot.yml` skeleton (shipctl sync will fill the job bodies).");
  } else {
    todos.push(`Author the CI workflow skeleton for \`${ci}\` manually (no renderer yet).`);
  }
  if (tracker !== "none") {
    todos.push(`Create the label contract for \`${tracker}\` (see preset:preset-${preset} for the label set).`);
  }
  for (const a of agents) {
    todos.push(`Agent rules for \`${a}\`: install via \`shipctl init --copy-rules --agents ${a}\`.`);
  }
  todos.push("Populate `.env.example` / secret store with the secrets listed above.");
  todos.push("Run `shipctl verify` after the above to confirm the stack.");
  return todos;
}

function buildRecommendedTools({ preset }) {
  const common = ["`shipctl doctor` — inspect repo and reconcile stack"];
  const byPreset = {
    "mobile-app": [
      "EAS Build / Fastlane for iOS + Android signed builds",
      "Detox or Maestro for device-farm E2E",
      "Expo Updates or CodePush for OTA patches",
    ],
    "web-app": [
      "Playwright (hosted) for PR preview E2E",
      "Preview deployments (Vercel / Netlify / Fly) per PR",
    ],
    "api-backend": [
      "Contract tests (Pact / OpenAPI diff)",
      "Migration discipline (Atlas / Liquibase)",
    ],
    cli: ["Cross-platform release matrix (GoReleaser / pkg / esbuild)"],
    monorepo: ["Turborepo / Nx / pnpm workspaces for per-package CI"],
    "adoption-minimum": [],
  };
  return [...common, ...(byPreset[preset] || [])];
}

function buildRecommendedSecrets({ tracker, ci }) {
  const secrets = new Set();
  if (tracker === "linear") secrets.add("LINEAR_API_KEY").add("LINEAR_TEAM_ID");
  if (tracker === "jira") secrets.add("JIRA_API_TOKEN").add("JIRA_EMAIL");
  if (tracker === "github-issues") secrets.add("GITHUB_TOKEN");
  if (ci === "gh-actions") secrets.add("GITHUB_TOKEN");
  if (ci === "circleci") secrets.add("CIRCLE_TOKEN");
  return [...secrets];
}

/**
 * Pick the right renderer for this stack. v1 only special-cases
 * `mobile-app + gh-actions + linear`.
 *
 * @param {object} cfg
 * @returns {RenderedPlan}
 */
export function renderPlan(cfg /*, presetArtifact */) {
  const preset = cfg.stack?.preset;
  const tracker = cfg.stack?.tracker;
  const ci = cfg.stack?.ci;

  if (preset === "mobile-app" && ci === "gh-actions" && tracker === "linear") {
    return renderMobileAppGhActionsLinear(cfg);
  }
  return renderAdoptionMinimum(cfg);
}

/**
 * Apply a plan to disk. Append-mode files use marker-guarded idempotency.
 * Create-mode files are skipped when they already exist unless `force`
 * is set (we never silently stomp a user's file).
 *
 * @param {string} cwd
 * @param {RenderedPlan} plan
 * @param {{dryRun?:boolean, force?:boolean}} [opts]
 * @returns {Array<{path:string, action:"wrote"|"skipped"|"appended"|"would_write"|"would_skip"|"would_append"}>}
 */
export function applyPlan(cwd, plan, opts = {}) {
  const { dryRun = false, force = false } = opts;
  /** @type {Array<{path:string, action:string}>} */
  const results = [];

  for (const file of plan.files) {
    const abs = path.join(cwd, file.path);

    if (file.mode === "append") {
      const current = fs.existsSync(abs) ? fs.readFileSync(abs, "utf8") : "";
      if (current.includes(ENV_EXAMPLE_MARKER_START)) {
        results.push({ path: file.path, action: dryRun ? "would_skip" : "skipped" });
        continue;
      }
      if (dryRun) {
        results.push({ path: file.path, action: "would_append" });
        continue;
      }
      fs.mkdirSync(path.dirname(abs), { recursive: true });
      const prefix = current.length && !current.endsWith("\n") ? "\n" : "";
      fs.writeFileSync(abs, current + prefix + file.content, "utf8");
      results.push({ path: file.path, action: "appended" });
      continue;
    }

    if (fs.existsSync(abs) && !force) {
      results.push({ path: file.path, action: dryRun ? "would_skip" : "skipped" });
      continue;
    }
    if (dryRun) {
      results.push({ path: file.path, action: "would_write" });
      continue;
    }
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, file.content, "utf8");
    results.push({ path: file.path, action: "wrote" });
  }

  return results;
}

/**
 * Top-level entry point used by `shipctl init --bootstrap`.
 *
 * @param {string} cwd
 * @param {object} config
 * @param {object|null} presetArtifact   Reserved for v2 when we parse the preset body.
 * @param {Array<object>} _adapters       Reserved for v2.
 * @param {{dryRun?:boolean, force?:boolean}} [opts]
 */
export function renderBootstrap(cwd, config, presetArtifact, _adapters, opts = {}) {
  const plan = renderPlan(config, presetArtifact);
  const results = applyPlan(cwd, plan, opts);
  return { plan, results };
}
