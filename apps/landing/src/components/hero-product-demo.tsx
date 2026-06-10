"use client";

import { useEffect, useState } from "react";

type DemoStage = "idea" | "build" | "preview";

const STAGES: DemoStage[] = ["idea", "build", "preview"];
const STAGE_MS = 3200;

/**
 * Lightweight CSS demo of Idea → Button → Site Preview.
 * Respects prefers-reduced-motion with a static three-panel layout.
 */
export function HeroProductDemo() {
  const [stage, setStage] = useState<DemoStage>("idea");
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (reducedMotion) return;

    const id = window.setInterval(() => {
      setStage((current) => {
        const idx = STAGES.indexOf(current);
        return STAGES[(idx + 1) % STAGES.length];
      });
    }, STAGE_MS);

    return () => window.clearInterval(id);
  }, [reducedMotion]);

  const active = reducedMotion ? null : stage;

  return (
    <figure
      className="mt-12 sm:mt-14"
      aria-label="Product demo: describe your idea, Ship builds it, preview your live site"
    >
      <div className="relative rounded-3xl border border-white/10 bg-gradient-to-br from-aqua/[0.07] via-white/[0.02] to-coral/[0.06] p-px shadow-[0_50px_120px_-30px_rgba(207,169,107,0.30)]">
        <div className="overflow-hidden rounded-[calc(1.5rem-1px)] bg-[#05060d] ring-1 ring-black/40">
          <div className="grid grid-cols-1 gap-0 md:grid-cols-3">
            <DemoPanel
              label="Idea"
              step={1}
              active={active === "idea" || reducedMotion}
              reducedMotion={reducedMotion}
            >
              <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/45">
                Describe your app
              </p>
              <div className="mt-3 space-y-2 rounded-xl border border-white/10 bg-black/40 p-3">
                <p className="text-sm leading-relaxed text-white/85">
                  A waitlist page for founders. Email capture, coral accents, mobile-first.
                </p>
                <div className="flex gap-2">
                  <span className="h-2 w-16 rounded-full bg-white/10" aria-hidden />
                  <span className="h-2 w-10 rounded-full bg-white/10" aria-hidden />
                </div>
              </div>
            </DemoPanel>

            <DemoPanel
              label="Button"
              step={2}
              active={active === "build" || reducedMotion}
              reducedMotion={reducedMotion}
            >
              <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/45">
                Ship builds it
              </p>
              <div className="mt-3 flex flex-col items-center gap-3 rounded-xl border border-aqua/25 bg-aqua/[0.06] p-4">
                <div
                  className={`inline-flex min-h-[44px] items-center justify-center rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-6 py-2.5 text-sm font-semibold text-ink ${
                    active === "build" && !reducedMotion ? "hero-demo-pulse" : ""
                  }`}
                >
                  Ship it
                </div>
                <ul className="w-full space-y-1.5 font-mono text-[10px] text-white/55">
                  <li className="flex items-center gap-2">
                    <span className="text-aqua">✓</span> Scaffold landing page
                  </li>
                  <li className="flex items-center gap-2">
                    <span className={active === "build" && !reducedMotion ? "text-sun" : "text-white/30"}>
                      {active === "build" && !reducedMotion ? "▸" : "·"}
                    </span>
                    Wire waitlist form
                  </li>
                  <li className="flex items-center gap-2">
                    <span className="text-white/30">·</span> Deploy preview
                  </li>
                </ul>
              </div>
            </DemoPanel>

            <DemoPanel
              label="Site Preview"
              step={3}
              active={active === "preview" || reducedMotion}
              reducedMotion={reducedMotion}
            >
              <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/45">
                Go live
              </p>
              <div className="mt-3 overflow-hidden rounded-xl border border-white/10 bg-gradient-to-b from-white/[0.04] to-transparent">
                <div className="flex items-center gap-1.5 border-b border-white/10 px-3 py-2">
                  <span className="h-2 w-2 rounded-full bg-coral/60" aria-hidden />
                  <span className="h-2 w-2 rounded-full bg-sun/60" aria-hidden />
                  <span className="h-2 w-2 rounded-full bg-aqua/60" aria-hidden />
                  <span className="ml-2 truncate font-mono text-[9px] text-white/40">
                    your-app.ship.app
                  </span>
                </div>
                <div className="space-y-2 p-3">
                  <div className="h-3 w-3/4 rounded bg-gradient-to-r from-coral/40 to-aqua/40" />
                  <div className="h-2 w-full rounded bg-white/10" />
                  <div className="h-2 w-5/6 rounded bg-white/10" />
                  <div className="mt-2 h-7 w-24 rounded-full bg-aqua/80" />
                </div>
              </div>
            </DemoPanel>
          </div>
        </div>
      </div>
      <figcaption className="mt-4 text-center text-xs text-white/45">
        Describe your idea in plain English — Ship scaffolds, builds, and ships a preview you can share.
      </figcaption>
    </figure>
  );
}

function DemoPanel({
  label,
  step,
  active,
  reducedMotion,
  children,
}: {
  label: string;
  step: number;
  active: boolean;
  reducedMotion: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`border-b border-white/10 p-5 transition-colors last:border-b-0 md:border-b-0 md:border-r md:last:border-r-0 ${
        active ? "bg-white/[0.03] ring-1 ring-inset ring-aqua/20" : "bg-transparent"
      }`}
      data-stage={label}
      aria-current={active ? "step" : undefined}
    >
      <div className="mb-3 flex items-center gap-2">
        <span
          className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold ${
            active ? "bg-aqua/20 text-aqua" : "bg-white/10 text-white/45"
          }`}
        >
          {step}
        </span>
        <span className="font-display text-sm font-bold text-white">{label}</span>
        {active && !reducedMotion && (
          <span className="ml-auto inline-flex h-2 w-2 rounded-full bg-aqua hero-demo-glow" aria-hidden />
        )}
      </div>
      {children}
    </div>
  );
}
