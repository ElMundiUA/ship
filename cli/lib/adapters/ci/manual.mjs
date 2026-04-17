export const id = "manual";
export const kind = "ci";

export async function detect() {
  return {
    present: true,
    confidence: 0.05,
    evidence: [{ type: "fallback", where: "-", match: "no CI system detected" }],
  };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
