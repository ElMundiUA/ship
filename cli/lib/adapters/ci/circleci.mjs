import { isFile } from "../_fs.mjs";

export const id = "circleci";
export const kind = "ci";

export async function detect(cwd) {
  if (isFile(cwd, ".circleci", "config.yml") || isFile(cwd, ".circleci", "config.yaml")) {
    return {
      present: true,
      confidence: 1,
      evidence: [{ type: "file", where: ".circleci/config.yml", match: "present" }],
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
