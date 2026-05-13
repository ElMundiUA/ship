import { isDir, listDir, readEnvFiles, readGithubWorkflows } from "../_fs.mjs";

export const id = "github-issues";
export const kind = "tracker";

export async function detect(cwd) {
  const evidence = [];
  let templateHit = false;
  let tokenHit = false;

  if (isDir(cwd, ".github", "ISSUE_TEMPLATE")) {
    const entries = listDir(cwd, ".github/ISSUE_TEMPLATE").filter((n) => !n.startsWith("."));
    templateHit = true;
    evidence.push({
      type: "dir",
      where: ".github/ISSUE_TEMPLATE/",
      match: `${entries.length} template(s)`,
    });
  }

  for (const { file, content } of readEnvFiles(cwd)) {
    if (/\bGITHUB_TOKEN\b/.test(content)) {
      tokenHit = true;
      evidence.push({ type: "env", where: file, match: "GITHUB_TOKEN" });
      break;
    }
  }
  if (!tokenHit) {
    for (const { file, content } of readGithubWorkflows(cwd)) {
      if (/\bGITHUB_TOKEN\b/.test(content)) {
        tokenHit = true;
        evidence.push({ type: "workflow", where: file, match: "GITHUB_TOKEN" });
        break;
      }
    }
  }

  const present = templateHit || tokenHit;
  let confidence = 0;
  if (templateHit) confidence = 0.8;
  else if (tokenHit) confidence = 0.3;

  return { present, confidence, evidence };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
