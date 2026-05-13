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

/**
 * Categorise a cron expression for the Capacity calendar projection.
 *
 * - ``fixed`` — single specific minute + hour pair (or comma-separated
 *   list of fixed times). Renders as champagne dots on the day×time
 *   grid for the days the dow field allows.
 * - ``highfreq`` — minute or hour field uses a wildcard or a "every N"
 *   step so the routine fires many times per day. Doesn't fit a single
 *   grid cell; renders in the Continuous-routines row instead.
 * - ``invalid`` — cron we couldn't parse at all.
 */
export type CronShape =
  | { kind: "fixed"; slots: Array<{ weekday: number; time: string }>; cadence: string }
  | { kind: "highfreq"; cadence: string }
  | { kind: "invalid" };

export function classifyCron(cron: string | null | undefined): CronShape {
  if (!cron?.trim()) return { kind: "invalid" };
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return { kind: "invalid" };
  const [minuteF, hourF, , , dowF] = fields;
  const minutes = explodeField(minuteF, 0, 59);
  const hours = explodeField(hourF, 0, 23);
  if (!minutes || !hours) return { kind: "invalid" };
  // High-frequency: more than 4 firings per day. The grid only shows
  // hourly slots; anything denser collapses into a "continuous" badge.
  if (minutes.length * hours.length > 4) {
    return { kind: "highfreq", cadence: humanCadence(minuteF, hourF) };
  }
  const weekdays = explodeField(dowF, 0, 6) ?? [0, 1, 2, 3, 4, 5, 6];
  const slots: Array<{ weekday: number; time: string }> = [];
  for (const w of weekdays) {
    for (const h of hours) {
      for (const m of minutes) {
        slots.push({
          weekday: w,
          time: `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`,
        });
      }
    }
  }
  return { kind: "fixed", slots, cadence: humanCadence(minuteF, hourF) };
}

function explodeField(s: string, lo: number, hi: number): number[] | null {
  if (s === "*" || s === "?") {
    const out: number[] = [];
    for (let v = lo; v <= hi; v += 1) out.push(v);
    return out;
  }
  if (s.includes(",")) {
    const parts = s.split(",").map((x) => x.trim());
    const out: number[] = [];
    for (const p of parts) {
      const sub = explodeField(p, lo, hi);
      if (!sub) return null;
      for (const v of sub) if (!out.includes(v)) out.push(v);
    }
    out.sort((a, b) => a - b);
    return out;
  }
  if (s.includes("/")) {
    const [r, st] = s.split("/");
    const step = parseInt(st ?? "1", 10);
    if (!Number.isFinite(step) || step < 1) return null;
    const start = r === "*" ? lo : parseInt(r, 10);
    if (!Number.isFinite(start)) return null;
    const out: number[] = [];
    for (let v = start; v <= hi; v += step) out.push(v);
    return out;
  }
  if (s.includes("-")) {
    const [a, b] = s.split("-").map((x) => parseInt(x.trim(), 10));
    if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
    const out: number[] = [];
    for (let v = a; v <= b; v += 1) out.push(v);
    return out;
  }
  const n = parseInt(s, 10);
  if (!Number.isFinite(n)) return null;
  return [n];
}

function humanCadence(minuteF: string, hourF: string): string {
  const minStep = stepOf(minuteF);
  const hourStep = stepOf(hourF);
  if (minuteF === "*" || (minStep && minStep < 60)) {
    if (minStep) return `every ${minStep} min`;
    if (minuteF === "*") return "every minute";
  }
  if (hourStep) return `every ${hourStep}h`;
  if (hourF === "*") return "every hour";
  return "fixed cadence";
}

function stepOf(field: string): number | null {
  if (!field.includes("/")) return null;
  const step = parseInt(field.split("/")[1] ?? "", 10);
  return Number.isFinite(step) ? step : null;
}
