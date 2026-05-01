import type { ReactNode } from "react";

/** Compact abstract motifs under each major part heading — same energy as the landing. */

function InsertShell({ children, gradient }: { children: ReactNode; gradient: string }) {
  return (
    <div
      className={`pointer-events-none my-8 flex h-20 items-center justify-center overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br ${gradient} px-6 opacity-95`}
      aria-hidden
    >
      <div className="w-full max-w-md text-white/35">{children}</div>
    </div>
  );
}

export function BookSectionInsert({ id }: { id: string }) {
  switch (id) {
    case "the-idea":
      return (
        <InsertShell gradient="from-coral/15 via-transparent to-lilac/10">
          <svg viewBox="0 0 400 72" className="h-full w-full" fill="none">
            <path d="M8 56 Q100 8 200 40 T392 28" stroke="currentColor" strokeWidth="2" opacity="0.5" />
            <circle cx="60" cy="44" r="6" fill="#cfa96b" opacity="0.5" />
            <circle cx="200" cy="32" r="8" fill="#ff5c6c" opacity="0.45" />
            <circle cx="340" cy="36" r="5" fill="#b388ff" opacity="0.55" />
          </svg>
        </InsertShell>
      );
    case "the-system":
      return (
        <InsertShell gradient="from-aqua/15 via-transparent to-coral/10">
          <svg viewBox="0 0 400 72" className="h-full w-full" fill="none">
            <rect x="24" y="16" width="72" height="40" rx="8" stroke="currentColor" strokeWidth="1.5" opacity="0.45" />
            <rect x="120" y="16" width="72" height="40" rx="8" stroke="currentColor" strokeWidth="1.5" opacity="0.35" />
            <rect x="216" y="16" width="72" height="40" rx="8" stroke="currentColor" strokeWidth="1.5" opacity="0.45" />
            <rect x="312" y="16" width="64" height="40" rx="8" stroke="currentColor" strokeWidth="1.5" opacity="0.3" />
            <path d="M96 36h16M192 36h16M288 36h16" stroke="#cfa96b" strokeWidth="2" opacity="0.4" />
          </svg>
        </InsertShell>
      );
    case "running-the-loop":
      return (
        <InsertShell gradient="from-sun/12 via-transparent to-aqua/12">
          <svg viewBox="0 0 400 72" className="h-full w-full" fill="none">
            <circle cx="80" cy="36" r="22" stroke="currentColor" strokeWidth="1.5" opacity="0.4" />
            <circle cx="200" cy="36" r="22" stroke="currentColor" strokeWidth="1.5" opacity="0.35" />
            <circle cx="320" cy="36" r="22" stroke="currentColor" strokeWidth="1.5" opacity="0.4" />
            <path d="M102 36h76M222 36h76" stroke="#ffd54a" strokeWidth="2" strokeDasharray="6 6" opacity="0.45" />
          </svg>
        </InsertShell>
      );
    case "trust-and-boundaries":
      return (
        <InsertShell gradient="from-lilac/18 via-transparent to-coral/8">
          <svg viewBox="0 0 400 72" className="h-full w-full" fill="none">
            <path d="M32 20h336v32H32z" stroke="currentColor" strokeWidth="1.5" rx="6" opacity="0.35" />
            <path d="M48 36h120M200 36h152" stroke="#b388ff" strokeWidth="2" opacity="0.4" />
            <circle cx="180" cy="36" r="10" fill="#cfa96b" opacity="0.25" />
          </svg>
        </InsertShell>
      );
    case "rolling-it-out":
      return (
        <InsertShell gradient="from-aqua/10 via-transparent to-sun/10">
          <svg viewBox="0 0 400 72" className="h-full w-full" fill="none">
            <path d="M20 52 L100 20 L180 44 L260 16 L340 48 L380 28" stroke="currentColor" strokeWidth="2" opacity="0.4" fill="none" />
            <circle cx="100" cy="20" r="5" fill="#cfa96b" opacity="0.5" />
            <circle cx="260" cy="16" r="5" fill="#ff5c6c" opacity="0.45" />
          </svg>
        </InsertShell>
      );
    case "when-things-break":
      return (
        <InsertShell gradient="from-coral/18 via-transparent to-ink">
          <svg viewBox="0 0 400 72" className="h-full w-full" fill="none">
            <path d="M40 24l32 32M72 24L40 56M200 20v40M320 24l32 32M352 24l-32 32" stroke="#ff5c6c" strokeWidth="2" opacity="0.35" />
            <rect x="160" y="20" width="80" height="32" rx="6" stroke="currentColor" strokeWidth="1.2" opacity="0.3" />
          </svg>
        </InsertShell>
      );
    case "vocabulary":
      return (
        <InsertShell gradient="from-lilac/12 via-transparent to-aqua/10">
          <svg viewBox="0 0 400 72" className="h-full w-full" fill="none">
            <text x="24" y="44" fill="currentColor" opacity="0.35" fontSize="22" fontFamily="system-ui" fontWeight="700">
              A→Z
            </text>
            <path d="M96 48h260" stroke="currentColor" strokeWidth="1.2" opacity="0.25" strokeDasharray="4 8" />
          </svg>
        </InsertShell>
      );
    default:
      return null;
  }
}
