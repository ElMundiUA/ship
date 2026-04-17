import { isFile } from "../_fs.mjs";

export const id = "gitlab-ci";
export const kind = "ci";

export async function detect(cwd) {
  if (isFile(cwd, ".gitlab-ci.yml")) {
    return {
      present: true,
      confidence: 1,
      evidence: [{ type: "file", where: ".gitlab-ci.yml", match: "present" }],
    };
  }
  return { present: false, confidence: 0, evidence: [] };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
