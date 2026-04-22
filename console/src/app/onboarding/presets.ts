/**
 * Preset catalog — shared between the old step-2 form handler and
 * the new wizard v2 per-repo configure card.
 *
 * Must stay in lockstep with
 * ``backend.app.services.lane_recipes.KNOWN_PRESETS``. Ordering
 * here drives picker order; ``adoption-minimum`` sits last because
 * it's the "I'll wire the rest later" option.
 */

export type PresetId =
  | "web-app"
  | "api-backend"
  | "mobile-app"
  | "mobile-app-deep"
  | "ml-project"
  | "platform"
  | "regulated"
  | "cli"
  | "monorepo"
  | "marketing"
  | "adoption-minimum";

export const PRESET_IDS: PresetId[] = [
  "web-app",
  "api-backend",
  "mobile-app",
  "mobile-app-deep",
  "ml-project",
  "platform",
  "regulated",
  "cli",
  "monorepo",
  "marketing",
  "adoption-minimum",
];

export const PRESET_META: Record<
  PresetId,
  { name: string; blurb: string; lanes: string }
> = {
  "web-app": {
    name: "Web app",
    blurb:
      "Next.js / Remix / SPA — full Elmundi-grade SDLC: PR review gate, daily standup, tech-debt scan, self-heal, code map.",
    lanes: "PR gate · Standup · Tech-debt · Self-heal · Code map",
  },
  "api-backend": {
    name: "API backend",
    blurb:
      "FastAPI / Go / Rails service — identical operational baseline as web-app, tailored for server repos.",
    lanes: "PR gate · Standup · Tech-debt · Code map",
  },
  "mobile-app": {
    name: "Mobile app",
    blurb:
      "iOS / Android / RN — same four lanes; hosted E2E ships once a device-lab preset lands.",
    lanes: "PR gate · Standup · Tech-debt · Code map",
  },
  "mobile-app-deep": {
    name: "Mobile app — deep",
    blurb:
      "iOS / Android with the full mobile pack: app-size & crash-rate gates, permissions audit, i18n sweeps, store submission + beta distribution flows, and a native-code reviewer.",
    lanes: "PR gate · Standup · Tech-debt · Crash monitor · Store submit",
  },
  "ml-project": {
    name: "ML project",
    blurb:
      "Training / inference / data pipelines — model eval gate, data-drift monitor, repro smoke test, feature-schema diff, fairness scanner, model-card flow, and an ML-aware reviewer.",
    lanes: "PR gate · Standup · Tech-debt · Drift monitor · Model card",
  },
  platform: {
    name: "Platform / SRE",
    blurb:
      "Infra repos with Terraform / Kubernetes / SLOs — drift monitor, policy gate, SLO burn paging, SBOM diff at release, cost-delta and blast-radius comments per PR, runbook-freshness sweep, on-call handoff flow.",
    lanes: "PR gate · Standup · Tech-debt · Drift · SLO burn · Blast radius",
  },
  regulated: {
    name: "Regulated industry",
    blurb:
      "Fintech / healthtech / SOC2 / HIPAA / PCI — PII leakage sweep, IAM policy-diff review, hourly audit-log integrity check, consent-coverage drift, and a compliance-artifact refresh flow per audit window.",
    lanes: "PR gate · Standup · PII sweep · IAM diff · Audit integrity",
  },
  cli: {
    name: "CLI / library",
    blurb:
      "CLI tools or libraries — quieter cadence: PR gate + tech-debt + code map only.",
    lanes: "PR gate · Tech-debt · Code map",
  },
  monorepo: {
    name: "Monorepo",
    blurb:
      "Large multi-package repo — opts into pipeline self-heal on top of the baseline.",
    lanes: "PR gate · Standup · Tech-debt · Self-heal · Code map",
  },
  marketing: {
    name: "Marketing site",
    blurb:
      "Landing pages, docs, blogs, campaign microsites — copy-first review, publishing-cadence standup, site-structure map.",
    lanes: "PR gate · Standup · Code map",
  },
  "adoption-minimum": {
    name: "Minimum",
    blurb:
      "Just the PR review gate + code map. Flip extra lanes on later from the Pipelines page.",
    lanes: "PR gate · Code map",
  },
};
