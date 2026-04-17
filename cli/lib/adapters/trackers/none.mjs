export const id = "none";
export const kind = "tracker";

export async function detect() {
  return {
    present: true,
    confidence: 0.05,
    evidence: [{ type: "fallback", where: "-", match: "no tracker detected" }],
  };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
