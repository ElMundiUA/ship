import * as linear from "./trackers/linear.mjs";
import * as jira from "./trackers/jira.mjs";
import * as githubIssues from "./trackers/github-issues.mjs";
import * as azureBoards from "./trackers/azure-boards.mjs";
import * as clickup from "./trackers/clickup.mjs";
import * as spreadsheet from "./trackers/spreadsheet.mjs";
import * as trackerNone from "./trackers/none.mjs";

import * as ghActions from "./ci/gh-actions.mjs";
import * as gitlabCi from "./ci/gitlab-ci.mjs";
import * as buildkite from "./ci/buildkite.mjs";
import * as circleci from "./ci/circleci.mjs";
import * as azurePipelines from "./ci/azure-pipelines.mjs";
import * as jenkins from "./ci/jenkins.mjs";
import * as ciManual from "./ci/manual.mjs";

import * as ts from "./language/ts.mjs";
import * as js from "./language/js.mjs";
import * as py from "./language/py.mjs";
import * as go from "./language/go.mjs";
import * as rust from "./language/rust.mjs";
import * as java from "./language/java.mjs";
import * as kotlin from "./language/kotlin.mjs";
import * as swift from "./language/swift.mjs";
import * as dart from "./language/dart.mjs";

import { detectAllAgents } from "./agents/index.mjs";

/** @typedef {{id:string, kind:string, detect:(cwd:string)=>Promise<{present:boolean,confidence:number,evidence:Array}>}} Adapter */

export const trackers = Object.freeze({
  linear,
  jira,
  "github-issues": githubIssues,
  "azure-boards": azureBoards,
  clickup,
  spreadsheet,
  none: trackerNone,
});

export const ci = Object.freeze({
  "gh-actions": ghActions,
  "gitlab-ci": gitlabCi,
  buildkite,
  circleci,
  "azure-pipelines": azurePipelines,
  jenkins,
  manual: ciManual,
});

export const language = Object.freeze({
  ts,
  js,
  py,
  go,
  rust,
  java,
  kotlin,
  swift,
  dart,
});

export { detectAllAgents };

function sortByConfidenceDesc(entries) {
  return [...entries].sort((a, b) => b.confidence - a.confidence || a.id.localeCompare(b.id));
}

/**
 * Run every adapter in every category against `cwd`, concurrently per
 * category, and return findings sorted by confidence descending.
 *
 * @param {string} cwd
 * @returns {Promise<{trackers:Array, ci:Array, language:Array, agents:Array}>}
 */
export async function detectAll(cwd) {
  const runOne = async (registry) => {
    const ids = Object.keys(registry);
    const entries = await Promise.all(
      ids.map(async (id) => {
        const adapter = registry[id];
        try {
          const r = await adapter.detect(cwd);
          return {
            id,
            present: !!r.present,
            confidence: typeof r.confidence === "number" ? r.confidence : 0,
            evidence: Array.isArray(r.evidence) ? r.evidence : [],
          };
        } catch (e) {
          return {
            id,
            present: false,
            confidence: 0,
            evidence: [
              {
                type: "error",
                where: "-",
                match: String(e && e.message ? e.message : e),
              },
            ],
          };
        }
      }),
    );
    return sortByConfidenceDesc(entries);
  };

  const [trackerResults, ciResults, languageResults, agentResults] = await Promise.all([
    runOne(trackers),
    runOne(ci),
    runOne(language),
    detectAllAgents(cwd),
  ]);

  return {
    trackers: trackerResults,
    ci: ciResults,
    language: languageResults,
    agents: sortByConfidenceDesc(agentResults),
  };
}
