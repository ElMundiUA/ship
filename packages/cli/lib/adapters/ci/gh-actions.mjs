import { isDir, listDir } from "../_fs.mjs";

export const id = "gh-actions";
export const kind = "ci";

export async function detect(cwd) {
  const evidence = [];
  if (!isDir(cwd, ".github", "workflows")) {
    return { present: false, confidence: 0, evidence };
  }
  const files = listDir(cwd, ".github/workflows").filter((f) => /\.ya?ml$/i.test(f));
  if (files.length === 0) {
    return { present: false, confidence: 0, evidence };
  }
  evidence.push({
    type: "dir",
    where: ".github/workflows/",
    match: `${files.length} workflow(s)`,
  });
  return { present: true, confidence: 1, evidence };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
