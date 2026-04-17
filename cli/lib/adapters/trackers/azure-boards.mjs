import { exists, readEnvFiles, readGithubWorkflows } from "../_fs.mjs";

export const id = "azure-boards";
export const kind = "tracker";

const ENV_KEYS = /\b(AZURE_DEVOPS_PAT|AZURE_DEVOPS_ORG|AZURE_DEVOPS_EXT_PAT)\b/;

export async function detect(cwd) {
  const evidence = [];
  let envHit = false;
  let ciHit = false;

  for (const { file, content } of readEnvFiles(cwd)) {
    const m = content.match(ENV_KEYS);
    if (m) {
      envHit = true;
      evidence.push({ type: "env", where: file, match: m[1] });
    }
  }

  if (exists(cwd, ".vsts-ci.yml")) {
    ciHit = true;
    evidence.push({ type: "file", where: ".vsts-ci.yml", match: "present" });
  }

  for (const { file, content } of readGithubWorkflows(cwd)) {
    if (/azure[-_ ]?boards/i.test(content) || /dev\.azure\.com/i.test(content)) {
      ciHit = true;
      evidence.push({ type: "workflow", where: file, match: "azure-boards reference" });
    }
  }

  const present = envHit || ciHit;
  let confidence = 0;
  if (present) {
    confidence = 0.7;
    if (envHit && ciHit) confidence = 0.95;
  }

  return { present, confidence, evidence };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
