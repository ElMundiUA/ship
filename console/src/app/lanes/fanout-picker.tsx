"use client";

/**
 * Fan-out picker for multi-pattern lanes (RFC-0008 C3.2).
 *
 * Only meaningful when a lane declares ≥2 patterns — the three
 * modes choose *how* the runner executes them:
 *
 * - ``matrix`` (default): one GitHub Actions job per pattern,
 *   parallel, isolated logs & artifacts. Faster wall-clock, burns
 *   more minutes.
 * - ``sequential``: one job, ``shipctl`` iterates the patterns
 *   in order. Cheapest and simplest; no parallelism.
 * - ``concurrent``: one job, ``shipctl`` spawns the patterns as
 *   parallel subprocesses. Faster than sequential but shares a
 *   single log stream.
 *
 * The backend only emits ``fanout`` to ``.ship/config.yml`` when
 * it differs from ``matrix`` (the default) so picking the default
 * produces a no-op diff.
 */

import type { FanoutMode } from "./config-draft";

const MODES: {
  id: FanoutMode;
  label: string;
  hint: string;
}[] = [
  {
    id: "matrix",
    label: "Parallel (matrix)",
    hint: "One GitHub job per pattern. Fastest wall-clock, isolated logs & artifacts. Default.",
  },
  {
    id: "sequential",
    label: "Sequential",
    hint: "One job; patterns run one after another. Cheapest; easy to follow in a single log.",
  },
  {
    id: "concurrent",
    label: "Concurrent (one job)",
    hint: "One job; patterns spawn in parallel as subprocesses. Faster than sequential, shared log.",
  },
];

export function FanoutPicker({
  patterns,
  value,
  onChange,
}: {
  patterns: string[];
  value: FanoutMode;
  onChange: (next: FanoutMode) => void;
}) {
  if (patterns.length < 2) return null;
  return (
    <div>
      <label className="block text-[10px] font-semibold uppercase tracking-widest text-white/55">
        How to run {patterns.length} patterns
      </label>
      <p className="mt-1 text-[11px] text-white/55">
        Patterns:{" "}
        {patterns.map((p, i) => (
          <span key={p}>
            <code className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[10px] text-white/80">
              {p}
            </code>
            {i < patterns.length - 1 ? " · " : null}
          </span>
        ))}
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {MODES.map((opt) => {
          const active = value === opt.id;
          return (
            <button
              key={opt.id}
              type="button"
              onClick={() => onChange(opt.id)}
              title={opt.hint}
              className={
                "rounded-full border px-3 py-1 text-[11px] font-semibold transition " +
                (active
                  ? "border-aqua/50 bg-aqua/15 text-aqua"
                  : "border-white/15 bg-white/[0.04] text-white/70 hover:border-white/30 hover:text-white")
              }
            >
              {opt.label}
            </button>
          );
        })}
      </div>
      <p className="mt-1 text-[11px] text-white/45">
        {MODES.find((m) => m.id === value)?.hint}
      </p>
    </div>
  );
}
