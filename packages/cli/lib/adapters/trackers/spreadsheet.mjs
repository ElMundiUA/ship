import { exists } from "../_fs.mjs";

export const id = "spreadsheet";
export const kind = "tracker";

export async function detect(cwd) {
  const evidence = [];
  let hit = false;

  if (exists(cwd, ".ship", "tracker-sheet.csv")) {
    hit = true;
    evidence.push({ type: "file", where: ".ship/tracker-sheet.csv", match: "present" });
  }
  if (exists(cwd, "tracker.xlsx")) {
    hit = true;
    evidence.push({ type: "file", where: "tracker.xlsx", match: "present" });
  }

  return { present: hit, confidence: hit ? 0.9 : 0, evidence };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
