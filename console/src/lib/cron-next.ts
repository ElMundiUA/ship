/**
 * Best-effort next run for standard 5-field cron (minute hour dom month dow, UTC).
 */

export function nextCronRun(
  cron: string | null | undefined,
  from: Date = new Date(),
): Date | null {
  if (!cron?.trim()) return null;
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return null;

  const start = new Date(from.getTime() + 60_000);
  start.setSeconds(0, 0);

  for (let i = 0; i < 60 * 24 * 14; i += 1) {
    const t = new Date(start.getTime() + i * 60_000);
    if (matchesCron(t, fields)) return t;
  }
  return null;
}

function matchesCron(t: Date, f: string[]): boolean {
  const m = t.getUTCMinutes();
  const h = t.getUTCHours();
  const dom = t.getUTCDate();
  const mon = t.getUTCMonth() + 1;
  const dow = t.getUTCDay();
  if (!fieldMatches(f[0] ?? "", m)) return false;
  if (!fieldMatches(f[1] ?? "", h)) return false;
  if (f[2] !== "?" && f[2] != null && f[2] !== "") {
    if (!fieldMatches(f[2], dom)) return false;
  }
  if (!fieldMatches(f[3] ?? "", mon)) return false;
  if (!fieldMatchesDow(f[4] ?? "", dow)) return false;
  return true;
}

function fieldMatchesDow(s: string, value: number): boolean {
  if (s === "*" || s === "?") return true;
  return fieldMatches(s, value);
}

function fieldMatches(s: string, value: number): boolean {
  if (s === "*" || s === "?") return true;
  if (s.includes(",")) {
    return s
      .split(",")
      .map((x) => x.trim())
      .some((part) => fieldMatchesSingle(part, value));
  }
  return fieldMatchesSingle(s, value);
}

function fieldMatchesSingle(s: string, value: number): boolean {
  if (s === "") return true;
  if (s === "*") return true;
  if (s.includes("/")) {
    const [r, st] = s.split("/");
    const start = r === "*" ? 0 : parseInt(r, 10);
    const step = parseInt(st ?? "1", 10);
    if (!Number.isFinite(step) || step < 1) return false;
    if (r === "*") return value % step === 0;
    return value >= start && (value - start) % step === 0;
  }
  if (s.includes("-")) {
    const [a, b] = s.split("-").map((x) => parseInt(x.trim(), 10));
    if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
    return value >= a && value <= b;
  }
  const n = parseInt(s, 10);
  return Number.isFinite(n) && n === value;
}

export function formatNextRun(cron: string | null | undefined): string {
  const next = nextCronRun(cron);
  if (!next) return "—";
  return next.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
