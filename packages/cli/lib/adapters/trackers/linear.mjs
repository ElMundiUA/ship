import {
  exists,
  pkgDeps,
  readEnvFiles,
  readGithubWorkflows,
  readJson,
} from "../_fs.mjs";

export const id = "linear";
export const kind = "tracker";

export async function detect(cwd) {
  const evidence = [];
  let envHit = false;
  let workflowHit = false;
  let packageHit = false;

  for (const { file, content } of readEnvFiles(cwd)) {
    if (/\bLINEAR_API_KEY\b/.test(content)) {
      envHit = true;
      evidence.push({ type: "env", where: file, match: "LINEAR_API_KEY" });
    }
  }

  const pkg = readJson(cwd, "package.json");
  const deps = pkgDeps(pkg);
  for (const name of Object.keys(deps)) {
    if (name.startsWith("@linear/")) {
      packageHit = true;
      evidence.push({ type: "package", where: "package.json", match: name });
    }
  }

  for (const { file, content } of readGithubWorkflows(cwd)) {
    if (/\blinear\b/i.test(content)) {
      workflowHit = true;
      evidence.push({ type: "workflow", where: file, match: "linear reference" });
    }
  }

  if (exists(cwd, ".linear")) {
    packageHit = true;
    evidence.push({ type: "dir", where: ".linear/", match: "present" });
  }

  const present = envHit || workflowHit || packageHit;
  let confidence = 0;
  if (present) {
    confidence = 0.7;
    if (envHit && (workflowHit || packageHit)) confidence = 0.95;
  }

  return { present, confidence, evidence };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
