/**
 * Cron helpers — just enough to drive the Lanes UX.
 *
 * We intentionally do NOT ship a full cron parser. The wizard only
 * emits a narrow slice of cron expressions (Daily / Weekly / Monthly
 * at a specific HH:MM UTC) and the calendar only needs to know
 * "when does this run over the next 7 days?". Users who hand-write
 * exotic cron (for example ``*\/15 * * * 1-5`` — every 15 minutes on
 * weekdays) will still see a fallback label and the block on the
 * calendar at the *approximate* next slot.
 *
 * UTC everywhere — GitHub Actions ``schedule:`` is UTC, so is this.
 */

export type Freq = "daily" | "weekly" | "monthly" | "custom";

export type ScheduleSpec =
  // Most common flavour the wizard produces.
  | { kind: "daily"; hour: number; minute: number }
  | { kind: "weekly"; hour: number; minute: number; weekdays: number[] }
  | { kind: "monthly"; hour: number; minute: number; dayOfMonth: number }
  // Anything the wizard can't parse back into Frequency form.
  | { kind: "custom"; cron: string };

// ----------------------------------------------------------------------------
// cron string <-> ScheduleSpec
// ----------------------------------------------------------------------------

/** Emit a 5-field cron from a wizard spec. Always UTC. */
export function specToCron(spec: ScheduleSpec): string {
  if (spec.kind === "custom") return spec.cron;
  const m = pad(spec.minute);
  const h = pad(spec.hour);
  if (spec.kind === "daily") return `${m} ${h} * * *`;
  if (spec.kind === "weekly") {
    const wd = [...new Set(spec.weekdays)].sort((a, b) => a - b).join(",");
    return `${m} ${h} * * ${wd || "*"}`;
  }
  return `${m} ${h} ${spec.dayOfMonth} * *`;
}

/**
 * Try to round-trip a cron string back to a wizard spec. Returns
 * ``{ kind: "custom" }`` if the cron doesn't match one of our
 * recognised shapes so the UI knows to show the advanced escape
 * hatch instead of lying with "Weekly at 09:00".
 */
export function cronToSpec(cron: string | null | undefined): ScheduleSpec {
  if (!cron) return { kind: "custom", cron: "" };
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return { kind: "custom", cron };
  const [minRaw, hourRaw, domRaw, monthRaw, dowRaw] = parts;

  const minute = singleInt(minRaw);
  const hour = singleInt(hourRaw);
  if (minute === null || hour === null) return { kind: "custom", cron };
  // We only render fixed HH:MM — reject anything using ranges/lists in
  // the minute/hour slots.
  if (minute < 0 || minute > 59 || hour < 0 || hour > 23) {
    return { kind: "custom", cron };
  }

  // Month field must be wildcard for us to recognise it (Outlook-like
  // UI has no "only in January" in MVP).
  if (monthRaw !== "*") return { kind: "custom", cron };

  // Monthly: ``dom`` specific, dow wildcard.
  if (domRaw !== "*" && dowRaw === "*") {
    const dom = singleInt(domRaw);
    if (dom === null || dom < 1 || dom > 31) return { kind: "custom", cron };
    return { kind: "monthly", hour, minute, dayOfMonth: dom };
  }

  // Daily: both dom and dow wildcard.
  if (domRaw === "*" && dowRaw === "*") {
    return { kind: "daily", hour, minute };
  }

  // Weekly: dom wildcard, dow a list/range/single.
  if (domRaw === "*" && dowRaw !== "*") {
    const days = parseDowField(dowRaw);
    if (!days) return { kind: "custom", cron };
    return { kind: "weekly", hour, minute, weekdays: days };
  }

  return { kind: "custom", cron };
}

// ----------------------------------------------------------------------------
// Cron matching (for the weekly calendar)
// ----------------------------------------------------------------------------

/**
 * Return all occurrences of ``cron`` that fall within the inclusive
 * window ``[from, to]``. Resolution is one minute (we step by the
 * minute slot the cron specifies on each day, which is enough for
 * our UI since we never schedule multiple times per hour in the
 * wizard).
 *
 * ``from`` / ``to`` are interpreted in UTC.
 */
export function nextOccurrences(
  cron: string,
  from: Date,
  to: Date,
): Date[] {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return [];
  const [minRaw, hourRaw, domRaw, monthRaw, dowRaw] = parts;
  const minutes = parseField(minRaw, 0, 59);
  const hours = parseField(hourRaw, 0, 23);
  const doms = parseField(domRaw, 1, 31);
  const months = parseField(monthRaw, 1, 12);
  const dows = parseField(dowRaw, 0, 6); // 0 = Sun, 6 = Sat
  if (!minutes || !hours || !doms || !months || !dows) return [];

  const out: Date[] = [];
  const cursor = new Date(from.getTime());
  cursor.setUTCSeconds(0, 0);

  // Hard stop at 10k iterations so a pathological cron doesn't hang
  // the browser — 7 days × 24h × 60m = 10080 iterations for minute-
  // resolution, which is our intended upper bound.
  for (let i = 0; i < 11000 && cursor.getTime() <= to.getTime(); i += 1) {
    const month = cursor.getUTCMonth() + 1;
    const dom = cursor.getUTCDate();
    const dow = cursor.getUTCDay();
    if (
      months.has(month) &&
      // cron OR semantics: if both dom and dow are restricted,
      // either matching is enough.
      matchesDomDow(doms, dows, domRaw, dowRaw, dom, dow) &&
      hours.has(cursor.getUTCHours()) &&
      minutes.has(cursor.getUTCMinutes())
    ) {
      out.push(new Date(cursor.getTime()));
    }
    cursor.setUTCMinutes(cursor.getUTCMinutes() + 1);
  }
  return out;
}

function matchesDomDow(
  doms: Set<number>,
  dows: Set<number>,
  domRaw: string,
  dowRaw: string,
  dom: number,
  dow: number,
): boolean {
  const domRestricted = domRaw !== "*";
  const dowRestricted = dowRaw !== "*";
  if (!domRestricted && !dowRestricted) return true;
  if (domRestricted && !dowRestricted) return doms.has(dom);
  if (!domRestricted && dowRestricted) return dows.has(dow);
  return doms.has(dom) || dows.has(dow);
}

// ----------------------------------------------------------------------------
// Human-readable cron label
// ----------------------------------------------------------------------------

const DAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_LONG = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

export function humanizeCron(cron: string | null | undefined): string {
  if (!cron) return "(no schedule)";
  const spec = cronToSpec(cron);
  if (spec.kind === "custom") return cron;
  const hhmm = `${pad(spec.hour)}:${pad(spec.minute)} UTC`;
  if (spec.kind === "daily") return `Daily at ${hhmm}`;
  if (spec.kind === "monthly") {
    return `Monthly on day ${spec.dayOfMonth} at ${hhmm}`;
  }
  // weekly
  const days = spec.weekdays.map((d) => DAY_SHORT[d]);
  const weekdaysOnly = [1, 2, 3, 4, 5];
  const weekendOnly = [0, 6];
  const allDays = [0, 1, 2, 3, 4, 5, 6];
  const sorted = [...spec.weekdays].sort((a, b) => a - b);
  if (setEq(sorted, weekdaysOnly)) return `Weekdays at ${hhmm}`;
  if (setEq(sorted, weekendOnly)) return `Weekends at ${hhmm}`;
  if (setEq(sorted, allDays)) return `Every day at ${hhmm}`;
  if (days.length === 1) return `Every ${DAY_LONG[spec.weekdays[0]]} at ${hhmm}`;
  return `${days.join(", ")} at ${hhmm}`;
}

// ----------------------------------------------------------------------------
// Validation
// ----------------------------------------------------------------------------

/**
 * Very forgiving — we accept any cron that looks like 5 whitespace-
 * separated fields of digits/ranges/lists/wildcards/steps. Backend
 * re-validates on propose (regex + GitHub workflow parser), so this
 * is only a client-side "did you even type something cron-shaped".
 */
export function isValidCron(cron: string): boolean {
  const s = cron.trim();
  if (!s) return false;
  const parts = s.split(/\s+/);
  if (parts.length !== 5) return false;
  return parts.every((p) => /^[0-9*,\-/]+$/.test(p));
}

// ----------------------------------------------------------------------------
// Internals
// ----------------------------------------------------------------------------

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function setEq(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((v, i) => v === b[i]);
}

function singleInt(s: string): number | null {
  if (!/^[0-9]+$/.test(s)) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function parseDowField(s: string): number[] | null {
  // Accept "1-5", "1,3,5", "*", single "2".
  const days = parseField(s, 0, 6);
  if (!days) return null;
  return [...days].sort((a, b) => a - b);
}

function parseField(s: string, lo: number, hi: number): Set<number> | null {
  if (s === "*") {
    const out = new Set<number>();
    for (let i = lo; i <= hi; i += 1) out.add(i);
    return out;
  }
  const out = new Set<number>();
  for (const piece of s.split(",")) {
    const [rangePart, stepPart] = piece.split("/");
    const step = stepPart ? Number(stepPart) : 1;
    if (!Number.isFinite(step) || step < 1) return null;
    let from = lo;
    let to = hi;
    if (rangePart !== "*") {
      if (rangePart.includes("-")) {
        const [a, b] = rangePart.split("-").map((x) => Number(x));
        if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
        from = a;
        to = b;
      } else {
        const v = Number(rangePart);
        if (!Number.isFinite(v)) return null;
        from = v;
        to = v;
      }
    }
    if (from < lo || to > hi || from > to) return null;
    for (let i = from; i <= to; i += step) out.add(i);
  }
  return out;
}
