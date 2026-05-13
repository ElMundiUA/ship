import { isDir, listDir } from "../_fs.mjs";

export const id = "buildkite";
export const kind = "ci";

export async function detect(cwd) {
  if (!isDir(cwd, ".buildkite")) {
    return { present: false, confidence: 0, evidence: [] };
  }
  const files = listDir(cwd, ".buildkite");
  return {
    present: true,
    confidence: 1,
    evidence: [{ type: "dir", where: ".buildkite/", match: `${files.length} entry(ies)` }],
  };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
