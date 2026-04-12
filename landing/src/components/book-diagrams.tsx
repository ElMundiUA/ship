"use client";

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { cn } from "@/lib/cn";

type Point = { x: number; y: number };

function DiagramWindowChrome() {
  return (
    <div className="flex items-center gap-2 border-b border-white/10 bg-white/[0.05] px-3 py-2.5">
      <span className="inline-flex gap-1.5" aria-hidden>
        <span className="h-2.5 w-2.5 rounded-full bg-coral/85" />
        <span className="h-2.5 w-2.5 rounded-full bg-sun/85" />
        <span className="h-2.5 w-2.5 rounded-full bg-aqua/85" />
      </span>
      <span className="ml-2 font-mono text-[10px] uppercase tracking-widest text-white/35">ship / diagram</span>
    </div>
  );
}

function bezierPath(a: Point, b: Point): string {
  const pull = Math.min(160, Math.abs(b.x - a.x) * 0.5 + 48);
  const sx = Math.sign(b.x - a.x) || 1;
  const c1x = a.x + sx * pull;
  const c2x = b.x - sx * pull;
  return `M ${a.x} ${a.y} C ${c1x} ${a.y}, ${c2x} ${b.y}, ${b.x} ${b.y}`;
}

function useDiagramLayout<T extends string>(nodeIds: readonly T[]) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [pts, setPts] = useState<Partial<Record<T, Point>>>({});
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const measure = useCallback(() => {
    const root = rootRef.current;
    if (!root) return;
    const box = root.getBoundingClientRect();
    const next = {} as Partial<Record<T, Point>>;
    for (const id of nodeIds) {
      const el = root.querySelector<HTMLElement>(`[data-diagram-node="${id}"]`);
      if (!el) continue;
      const b = el.getBoundingClientRect();
      (next as Record<string, Point>)[id] = {
        x: b.left - box.left + b.width / 2,
        y: b.top - box.top + b.height / 2,
      };
    }
    setPts(next);
  }, [nodeIds]);

  useLayoutEffect(() => {
    if (!mounted) return;
    measure();
    const root = rootRef.current;
    if (!root) return;
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const ro = new ResizeObserver(() => measure());
    ro.observe(root);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [mounted, measure]);

  return { rootRef, pts, mounted };
}

type Edge<T extends string> = { id: string; from: T; to: T; label: string };

function highlightNodeSet<T extends string>(edges: Edge<T>[], active: string | null): Set<string> | null {
  if (!active) return null;
  const s = new Set<string>([active]);
  for (const e of edges) {
    if (e.from === active || e.to === active) {
      s.add(e.from);
      s.add(e.to);
    }
  }
  return s;
}

function EdgesSvg<T extends string>({
  edges,
  pts,
  active,
  gradientId,
}: {
  edges: Edge<T>[];
  pts: Partial<Record<T, Point>>;
  active: string | null;
  gradientId: string;
}) {
  const glow = highlightNodeSet(edges, active);
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full overflow-visible"
      aria-hidden
    >
      <defs>
        <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="rgba(46,230,214,0.35)" />
          <stop offset="50%" stopColor="rgba(179,136,255,0.55)" />
          <stop offset="100%" stopColor="rgba(255,92,108,0.35)" />
        </linearGradient>
      </defs>
      {edges.map((e) => {
        const a = pts[e.from];
        const b = pts[e.to];
        if (!a || !b) return null;
        const isOn = glow ? glow.has(e.from) && glow.has(e.to) : false;
        return (
          <g key={e.id}>
            <path
              d={bezierPath(a, b)}
              fill="none"
              stroke={`url(#${gradientId})`}
              strokeWidth={isOn ? 2.5 : 1.1}
              strokeOpacity={isOn ? 0.92 : 0.28}
              className="transition-all duration-300"
            />
          </g>
        );
      })}
    </svg>
  );
}

function DiagramNode({
  id,
  children,
  className,
  highlightSet,
  onHover,
}: {
  id: string;
  children: ReactNode;
  className?: string;
  highlightSet: Set<string> | null;
  onHover: (id: string | null) => void;
}) {
  const lit = !highlightSet || highlightSet.has(id);
  return (
    <div
      data-diagram-node={id}
      role="button"
      tabIndex={0}
      onMouseEnter={() => onHover(id)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(id)}
      onBlur={() => onHover(null)}
      className={cn(
        "cursor-default rounded-xl border px-2.5 py-2 text-center text-[11px] font-medium leading-snug text-white/90 shadow-sm transition-all duration-200 sm:text-xs",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-aqua/50",
        lit ? "opacity-100" : "opacity-[0.38]",
        className,
      )}
    >
      {children}
    </div>
  );
}

function RegionFrame({
  title,
  accent,
  children,
}: {
  title: string;
  accent: "human" | "gha" | "cursor" | "linear" | "neutral";
  children: ReactNode;
}) {
  const ring = {
    human: "border-fuchsia-400/35 bg-fuchsia-500/[0.08]",
    gha: "border-sky-400/35 bg-sky-500/[0.08]",
    cursor: "border-amber-400/40 bg-amber-500/[0.1]",
    linear: "border-indigo-400/35 bg-indigo-500/[0.1]",
    neutral: "border-white/15 bg-white/[0.04]",
  }[accent];
  const titleColor = {
    human: "text-fuchsia-200/90",
    gha: "text-sky-200/90",
    cursor: "text-amber-100/90",
    linear: "text-indigo-100/90",
    neutral: "text-white/80",
  }[accent];

  return (
    <div
      className={cn(
        "relative z-10 flex flex-col gap-3 rounded-2xl border p-3 sm:p-4",
        "bg-[linear-gradient(145deg,rgba(255,255,255,0.06),transparent)]",
        ring,
      )}
    >
      <div className={cn("font-display text-[11px] font-bold uppercase tracking-widest sm:text-xs", titleColor)}>
        {title}
      </div>
      {children}
    </div>
  );
}

const archNodeIds = [
  "human-backlog",
  "human-todo",
  "gha-sdlc",
  "gha-pick",
  "gha-audit",
  "gha-snyk",
  "gha-heal",
  "cursor-api",
  "cursor-run",
  "linear-pre",
  "linear-td",
  "linear-sec",
] as const;
type ArchNode = (typeof archNodeIds)[number];

const archEdges: Edge<ArchNode>[] = [
  { id: "e1", from: "human-todo", to: "linear-pre", label: "Work in scope" },
  { id: "e2", from: "gha-sdlc", to: "gha-pick", label: "On schedule" },
  { id: "e3", from: "gha-pick", to: "cursor-api", label: "Item id + prompt bundle" },
  { id: "e4", from: "gha-audit", to: "cursor-api", label: "Assurance pass (no delivery pick)" },
  { id: "e5", from: "gha-audit", to: "gha-snyk", label: "" },
  { id: "e6", from: "gha-snyk", to: "cursor-api", label: "Scan output when policy fails" },
  { id: "e7", from: "gha-heal", to: "cursor-api", label: "From CI signals + repo config" },
  { id: "e8", from: "cursor-api", to: "linear-pre", label: "Comments, states, reviews & merges" },
  { id: "e9", from: "cursor-run", to: "linear-td", label: "Audit findings" },
  { id: "e10", from: "cursor-run", to: "linear-sec", label: "Security follow-ups" },
];

export function SystemArchitectureDiagram({ caption }: { caption?: string }) {
  const [active, setActive] = useState<string | null>(null);
  const { rootRef, pts, mounted } = useDiagramLayout(archNodeIds);
  const gradUid = useId().replace(/:/g, "");
  const gradId = `edge-grad-arch-${gradUid}`;
  const archGlow = useMemo(() => highlightNodeSet(archEdges, active), [active]);

  const edgeLegend = useMemo(() => {
    if (!active) return "Hover a box to highlight flows.";
    const hit = archEdges.find((e) => e.from === active || e.to === active);
    if (!hit || !hit.label) {
      const all = archEdges.filter((e) => e.from === active || e.to === active);
      const labels = [...new Set(all.map((e) => e.label).filter(Boolean))];
      return labels.length ? labels.join(" · ") : "Part of the orchestration loop.";
    }
    return hit.label;
  }, [active]);

  return (
    <figure className="not-prose my-14">
      <div className="relative overflow-hidden rounded-2xl border border-white/12 bg-black/40 shadow-card">
        <DiagramWindowChrome />
        <div ref={rootRef} className="relative min-h-[420px] p-3 sm:min-h-[460px] sm:p-5">
          {mounted ? <EdgesSvg edges={archEdges} pts={pts} active={active} gradientId={gradId} /> : null}

          <div className="relative z-10 grid gap-3 lg:grid-cols-2 lg:gap-4">
            <RegionFrame title="Human & policy" accent="human">
              <div className="grid gap-2 sm:grid-cols-2">
                <DiagramNode
                  id="human-backlog"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-fuchsia-400/25 bg-fuchsia-950/30"
                >
                  Backlog (manual triage)
                </DiagramNode>
                <DiagramNode
                  id="human-todo"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-fuchsia-400/35 bg-fuchsia-900/25"
                >
                  Ready column + board filters
                </DiagramNode>
              </div>
            </RegionFrame>

            <RegionFrame title="CI / automation runner" accent="gha">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                <DiagramNode
                  id="gha-sdlc"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-sky-400/30 bg-sky-950/35 sm:col-span-1"
                >
                  Scheduled delivery workflow
                </DiagramNode>
                <DiagramNode
                  id="gha-pick"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-sky-400/40 bg-sky-900/30"
                >
                  Deterministic pick (script or job step)
                </DiagramNode>
                <DiagramNode
                  id="gha-audit"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-sky-400/30 bg-sky-950/35"
                >
                  Scheduled assurance / audit job
                </DiagramNode>
                <DiagramNode
                  id="gha-snyk"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-sky-400/30 bg-sky-950/35"
                >
                  Dependency / supply-chain check
                </DiagramNode>
                <DiagramNode
                  id="gha-heal"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-sky-400/35 bg-sky-900/25 sm:col-span-2"
                >
                  Self-heal on pipeline signals
                </DiagramNode>
              </div>
            </RegionFrame>
          </div>

          <div className="relative z-10 mt-3 lg:mt-4">
            <RegionFrame title="Managed agent runtime" accent="cursor">
              <div className="grid gap-2 sm:grid-cols-2">
                <DiagramNode
                  id="cursor-api"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-amber-400/40 bg-amber-950/35"
                >
                  Agent launch API
                </DiagramNode>
                <DiagramNode
                  id="cursor-run"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-amber-400/35 bg-amber-900/25"
                >
                  Isolated branch + tools
                </DiagramNode>
              </div>
            </RegionFrame>
          </div>

          <div className="relative z-10 mt-3 lg:mt-4">
            <RegionFrame title="Issue tracker (system of record)" accent="linear">
              <div className="grid gap-2 sm:grid-cols-3">
                <DiagramNode
                  id="linear-pre"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-indigo-400/35 bg-indigo-950/40"
                >
                  Primary delivery board / swimlane
                </DiagramNode>
                <DiagramNode
                  id="linear-td"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-indigo-400/35 bg-indigo-950/40"
                >
                  Tech health / debt lane
                </DiagramNode>
                <DiagramNode
                  id="linear-sec"
                  highlightSet={archGlow}
                  onHover={setActive}
                  className="border-indigo-400/35 bg-indigo-950/40"
                >
                  Security / findings lane
                </DiagramNode>
              </div>
            </RegionFrame>
          </div>

          <p className="relative z-10 mt-4 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-center text-[11px] leading-snug text-white/55 sm:text-xs">
            {edgeLegend}
          </p>
        </div>
      </div>
      {caption ? <figcaption className="mt-3 text-center text-sm text-white/45">{caption}</figcaption> : null}
    </figure>
  );
}

const sdlcNodeIds = ["h-bl", "b-td", "b-ip", "b-ir", "b-dn", "b-bk", "s-in", "s-cl", "s-ba", "s-dv"] as const;
type SdlcNode = (typeof sdlcNodeIds)[number];

const sdlcEdges: Edge<SdlcNode>[] = [
  { id: "s1", from: "h-bl", to: "b-td", label: "Promote to ready column" },
  { id: "s2", from: "b-td", to: "s-in", label: "Eligible pool" },
  { id: "s3", from: "b-td", to: "s-cl", label: "Eligible pool" },
  { id: "s4", from: "b-td", to: "s-ba", label: "Eligible pool" },
  { id: "s5", from: "b-td", to: "s-dv", label: "Pick when ready for implementation" },
  { id: "s6", from: "s-in", to: "b-td", label: "Metadata update · stay in ready" },
  { id: "s7", from: "s-cl", to: "b-td", label: "After answers → ready column" },
  { id: "s8", from: "s-ba", to: "b-td", label: "Spec + handoff → ready column" },
  { id: "s9", from: "s-dv", to: "b-ip", label: "Agent run · isolated branch" },
  { id: "s10", from: "b-ip", to: "b-ir", label: "Change up for review" },
  { id: "s11", from: "b-ir", to: "b-dn", label: "Released / accepted" },
  { id: "s12", from: "b-td", to: "b-bk", label: "Stop" },
  { id: "s13", from: "b-ip", to: "b-bk", label: "Stop" },
];

export function SdlcLaneDiagram({ caption }: { caption?: string }) {
  const [active, setActive] = useState<string | null>(null);
  const { rootRef, pts, mounted } = useDiagramLayout(sdlcNodeIds);
  const sdlcGradUid = useId().replace(/:/g, "");
  const sdlcGradId = `edge-grad-sdlc-${sdlcGradUid}`;
  const sdlcGlow = useMemo(() => highlightNodeSet(sdlcEdges, active), [active]);

  const hint = useMemo(() => {
    if (!active) return "Hover nodes to trace the cadence grid against board columns.";
    const labels = sdlcEdges
      .filter((e) => (e.from === active || e.to === active) && e.label)
      .map((e) => e.label);
    return [...new Set(labels)].join(" · ") || "Lane handoff";
  }, [active]);

  return (
    <figure className="not-prose my-14">
      <div className="relative overflow-hidden rounded-2xl border border-white/12 bg-black/40 shadow-card">
        <DiagramWindowChrome />
        <div ref={rootRef} className="relative min-h-[380px] p-3 sm:min-h-[400px] sm:p-5">
          {mounted ? <EdgesSvg edges={sdlcEdges} pts={pts} active={active} gradientId={sdlcGradId} /> : null}

          <div className="relative z-10 grid gap-3 lg:grid-cols-3 lg:gap-4">
            <RegionFrame title="Human gate" accent="human">
              <DiagramNode
                id="h-bl"
                highlightSet={sdlcGlow}
                onHover={setActive}
                className="border-fuchsia-400/30 bg-fuchsia-950/30"
              >
                Backlog
                <div className="mt-1 text-[10px] font-normal text-fuchsia-200/70">No pick — triage only</div>
              </DiagramNode>
            </RegionFrame>

            <RegionFrame title="Delivery board (tracker)" accent="linear">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                <DiagramNode id="b-td" highlightSet={sdlcGlow} onHover={setActive} className="border-indigo-400/35 bg-indigo-950/40">
                  Ready / next up
                </DiagramNode>
                <DiagramNode id="b-ip" highlightSet={sdlcGlow} onHover={setActive} className="border-indigo-400/35 bg-indigo-950/40">
                  In Progress
                </DiagramNode>
                <DiagramNode id="b-ir" highlightSet={sdlcGlow} onHover={setActive} className="border-indigo-400/35 bg-indigo-950/40">
                  In Review
                </DiagramNode>
                <DiagramNode id="b-dn" highlightSet={sdlcGlow} onHover={setActive} className="border-indigo-400/35 bg-indigo-950/40">
                  Done
                </DiagramNode>
                <DiagramNode
                  id="b-bk"
                  highlightSet={sdlcGlow}
                  onHover={setActive}
                  className="border-rose-400/35 bg-rose-950/30 sm:col-span-2"
                >
                  Blocked
                </DiagramNode>
              </div>
            </RegionFrame>

            <RegionFrame title="Role clock (example cadence)" accent="cursor">
              <div className="grid grid-cols-2 gap-2">
                <DiagramNode id="s-in" highlightSet={sdlcGlow} onHover={setActive} className="border-amber-400/35 bg-amber-950/35">
                  Slot A — intake
                </DiagramNode>
                <DiagramNode id="s-cl" highlightSet={sdlcGlow} onHover={setActive} className="border-amber-400/35 bg-amber-950/35">
                  Slot B — clarification
                </DiagramNode>
                <DiagramNode id="s-ba" highlightSet={sdlcGlow} onHover={setActive} className="border-amber-400/35 bg-amber-950/35">
                  Slot C — analysis / spec
                </DiagramNode>
                <DiagramNode id="s-dv" highlightSet={sdlcGlow} onHover={setActive} className="border-amber-400/40 bg-amber-900/30">
                  Slot D — implementation
                </DiagramNode>
              </div>
            </RegionFrame>
          </div>

          <div className="relative z-10 mt-4">
            <RegionFrame title="Label hints (happy path)" accent="neutral">
              <div className="grid gap-2 text-left text-[11px] text-white/70 sm:grid-cols-3 sm:text-xs">
                <div className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-2">
                  <span className="font-semibold text-aqua/90">Intake role</span> → triage toward clarification or staged intake
                </div>
                <div className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-2">
                  <span className="font-semibold text-lilac/90">Analysis / spec</span> → handoff, still in ready column
                </div>
                <div className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-2">
                  <span className="font-semibold text-coral/90">Implementation</span> → in progress only after pick
                </div>
              </div>
            </RegionFrame>
          </div>

          <p className="relative z-10 mt-4 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-center text-[11px] leading-snug text-white/55 sm:text-xs">
            {hint}
          </p>
        </div>
      </div>
      {caption ? <figcaption className="mt-3 text-center text-sm text-white/45">{caption}</figcaption> : null}
    </figure>
  );
}
