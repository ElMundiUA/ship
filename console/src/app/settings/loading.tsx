import { LoadingShell } from "@/components/loading-shell";

export default function Loading() {
  return (
    <LoadingShell>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <nav className="space-y-1">
          {Array.from({ length: 8 }).map((_, i) => (
            <div
              key={i}
              className="h-8 w-full animate-pulse rounded-md bg-white/[0.06]"
            />
          ))}
        </nav>
        <div className="space-y-4">
          <div className="h-32 animate-pulse rounded-2xl border border-white/10 bg-white/[0.04]" />
          <div className="h-48 animate-pulse rounded-2xl border border-white/10 bg-white/[0.04]" />
        </div>
      </div>
    </LoadingShell>
  );
}
