import {
  exists,
  readEnvFiles,
  readGithubWorkflows,
  readJson,
} from "../_fs.mjs";

export const id = "jira";
export const kind = "tracker";

const ENV_KEYS = /\b(JIRA_URL|JIRA_API_TOKEN|ATLASSIAN_TOKEN|ATLASSIAN_API_TOKEN)\b/;

export async function detect(cwd) {
  const evidence = [];
  let envHit = false;
  let workflowHit = false;
  let fileHit = false;

  for (const { file, content } of readEnvFiles(cwd)) {
    const m = content.match(ENV_KEYS);
    if (m) {
      envHit = true;
      evidence.push({ type: "env", where: file, match: m[1] });
    }
  }

  for (const { file, content } of readGithubWorkflows(cwd)) {
    if (/\bjira\b/i.test(content) || /atlassian/i.test(content)) {
      workflowHit = true;
      evidence.push({ type: "workflow", where: file, match: "jira/atlassian reference" });
    }
  }

  if (exists(cwd, ".jira")) {
    fileHit = true;
    evidence.push({ type: "dir", where: ".jira/", match: "present" });
  }
  if (exists(cwd, "atlassian-connect.json")) {
    fileHit = true;
    evidence.push({ type: "file", where: "atlassian-connect.json", match: "present" });
  }

  const pkg = readJson(cwd, "package.json");
  const scripts = pkg && typeof pkg === "object" ? pkg.scripts : null;
  if (scripts && typeof scripts === "object") {
    for (const [k, v] of Object.entries(scripts)) {
      if (typeof v === "string" && /\bjira\b/i.test(v)) {
        workflowHit = true;
        evidence.push({ type: "script", where: `package.json:scripts.${k}`, match: "jira reference" });
        break;
      }
    }
  }

  const present = envHit || workflowHit || fileHit;
  let confidence = 0;
  if (present) {
    confidence = 0.7;
    if (envHit && (workflowHit || fileHit)) confidence = 0.95;
    if (fileHit && workflowHit) confidence = Math.max(confidence, 0.85);
  }

  return { present, confidence, evidence };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
