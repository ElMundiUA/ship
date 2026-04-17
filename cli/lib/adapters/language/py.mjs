import { isFile, listDir } from "../_fs.mjs";

export const id = "py";
export const kind = "language";

export async function detect(cwd) {
  const evidence = [];
  let hit = false;

  if (isFile(cwd, "pyproject.toml")) {
    hit = true;
    evidence.push({ type: "file", where: "pyproject.toml", match: "present" });
  }
  if (isFile(cwd, "setup.py")) {
    hit = true;
    evidence.push({ type: "file", where: "setup.py", match: "present" });
  }
  for (const name of listDir(cwd, ".")) {
    if (/^requirements.*\.txt$/.test(name) && isFile(cwd, name)) {
      hit = true;
      evidence.push({ type: "file", where: name, match: "present" });
    }
  }

  return { present: hit, confidence: hit ? 1 : 0, evidence };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
