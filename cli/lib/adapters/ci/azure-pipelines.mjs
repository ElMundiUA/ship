import { isFile } from "../_fs.mjs";

export const id = "azure-pipelines";
export const kind = "ci";

export async function detect(cwd) {
  if (isFile(cwd, "azure-pipelines.yml") || isFile(cwd, "azure-pipelines.yaml")) {
    return {
      present: true,
      confidence: 1,
      evidence: [{ type: "file", where: "azure-pipelines.yml", match: "present" }],
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
