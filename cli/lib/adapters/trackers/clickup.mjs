import { pkgDeps, readEnvFiles, readJson } from "../_fs.mjs";

export const id = "clickup";
export const kind = "tracker";

export async function detect(cwd) {
  const evidence = [];
  let envHit = false;
  let packageHit = false;

  for (const { file, content } of readEnvFiles(cwd)) {
    if (/\bCLICKUP_API_TOKEN\b/.test(content)) {
      envHit = true;
      evidence.push({ type: "env", where: file, match: "CLICKUP_API_TOKEN" });
    }
  }

  const pkg = readJson(cwd, "package.json");
  const deps = pkgDeps(pkg);
  for (const name of Object.keys(deps)) {
    if (name.startsWith("@clickup/")) {
      packageHit = true;
      evidence.push({ type: "package", where: "package.json", match: name });
    }
  }

  const present = envHit || packageHit;
  let confidence = 0;
  if (present) {
    confidence = 0.7;
    if (envHit && packageHit) confidence = 0.95;
  }

  return { present, confidence, evidence };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
