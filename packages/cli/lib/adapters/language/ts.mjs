import { isFile, pkgDeps, readJson } from "../_fs.mjs";

export const id = "ts";
export const kind = "language";

export async function detect(cwd) {
  const evidence = [];
  let hit = false;

  if (isFile(cwd, "tsconfig.json")) {
    hit = true;
    evidence.push({ type: "file", where: "tsconfig.json", match: "present" });
  }

  const pkg = readJson(cwd, "package.json");
  const deps = pkgDeps(pkg);
  if (deps.typescript) {
    hit = true;
    evidence.push({
      type: "package",
      where: "package.json",
      match: `typescript@${deps.typescript}`,
    });
  }

  return { present: hit, confidence: hit ? 1 : 0, evidence };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
