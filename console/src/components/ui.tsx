import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Small, reusable bits of chrome shared across the console mock screens.
 *
 * Pulled into one file so individual page components stay focused on layout
 * and copy rather than re-defining badges/cards/empty states inline.
 */

export function Card({
  children,
  className,
  padded = true,
  id,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
  id?: string;
}) {
  return (
    <div
      id={id}
      className={cn(
        "rounded-2xl border border-white/10 bg-white/[0.04] backdrop-blur-xl shadow-card",
        padded && "p-5",
        className
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-4 flex items-start justify-between gap-3", className)}>
      <div className="min-w-0">
        <h3 className="font-display text-base font-bold text-white">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-white/55">{subtitle}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export type BadgeTone =
  | "neutral"
  | "global"
  | "workspace"
  | "project"
  | "ok"
  | "warn"
  | "err"
  | "info";

const BADGE_CLS: Record<BadgeTone, string> = {
  neutral: "bg-white/10 text-white/70 border-white/15",
  global: "bg-white/10 text-white/75 border-white/20",
  workspace: "bg-aqua/15 text-aqua border-aqua/40",
  project: "bg-lilac/15 text-lilac border-lilac/40",
  ok: "bg-emerald-500/15 text-emerald-300 border-emerald-400/30",
  warn: "bg-sun/15 text-sun border-sun/40",
  err: "bg-coral/15 text-coral border-coral/40",
  info: "bg-sky-500/15 text-sky-300 border-sky-400/30",
};

export function Badge({
  tone = "neutral",
  children,
  dot,
  className,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  dot?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
        BADGE_CLS[tone],
        className
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export function PageActions({ children }: { children: ReactNode }) {
  return <div className="flex items-center gap-2">{children}</div>;
}

export function ButtonGhost({
  children,
  onClick,
  className,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]",
        className
      )}
    >
      {children}
    </button>
  );
}

export function ButtonPrimary({
  children,
  onClick,
  className,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3.5 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110",
        className
      )}
    >
      {children}
    </button>
  );
}

export function ButtonDanger({
  children,
  onClick,
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-coral/40 bg-coral/10 px-3 py-1.5 text-xs font-semibold text-coral transition hover:border-coral/70 hover:bg-coral/20",
        className
      )}
    >
      {children}
    </button>
  );
}

export function StatTile({
  label,
  value,
  delta,
  hint,
}: {
  label: string;
  value: string;
  delta?: { sign: "up" | "down" | "flat"; pct: number };
  hint?: string;
}) {
  const arrow = delta?.sign === "up" ? "↑" : delta?.sign === "down" ? "↓" : "→";
  // "down = better" for some KPIs; component is intentionally agnostic and
  // just colours the badge based on raw direction. The hint copy on each KPI
  // explains "down = better" where it matters.
  const tone = delta?.sign === "up" ? "ok" : delta?.sign === "down" ? "info" : "neutral";
  return (
    <Card className="relative overflow-hidden">
      <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
        {label}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <div className="font-display text-3xl font-bold tracking-tight text-white">{value}</div>
        {delta && (
          <Badge tone={tone}>
            {arrow} {delta.pct}%
          </Badge>
        )}
      </div>
      {hint && <p className="mt-1.5 text-[11px] leading-snug text-white/45">{hint}</p>}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-aqua/10 to-transparent"
      />
    </Card>
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <Card className="grid place-items-center py-12 text-center">
      <div className="max-w-sm">
        <h4 className="font-display text-lg font-bold text-white">{title}</h4>
        <p className="mt-1.5 text-sm text-white/60">{body}</p>
        {action && <div className="mt-4">{action}</div>}
      </div>
    </Card>
  );
}

export function MockBanner({ reason }: { reason?: ReactNode } = {}) {
  return (
    <div className="mb-5 flex items-center gap-2 rounded-xl border border-sun/30 bg-sun/5 px-3 py-2 text-xs text-sun/95">
      <span className="rounded-full bg-sun/25 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-sun">
        mock
      </span>
      <span>
        {reason ?? (
          <>
            UI preview · backed by mock data. Real{" "}
            <code className="font-mono text-sun/90">/v1</code> wiring lands once
            the workspace + auth flow ships.
          </>
        )}
      </span>
    </div>
  );
}

export function LiveBanner({ workspace }: { workspace: string }) {
  return (
    <div className="mb-5 flex items-center gap-2 rounded-xl border border-aqua/30 bg-aqua/5 px-3 py-2 text-xs text-aqua/95">
      <span className="rounded-full bg-aqua/25 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua">
        live
      </span>
      <span>
        Reading from <code className="font-mono text-aqua/90">/v1</code> ·
        workspace <code className="font-mono text-aqua/90">{workspace}</code>.
        Resolver merges global + workspace + project layers per your{" "}
        <code className="font-mono text-aqua/90">catalog_sources</code> toggles.
      </span>
    </div>
  );
}
