import { LoadingBody } from "@/components/loading-shell";

export default function Loading() {
  return (
    <LoadingBody>
      <div className="space-y-4">
        <div className="h-64 animate-pulse rounded-2xl border border-white/10 bg-white/[0.04]" />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="h-40 animate-pulse rounded-2xl border border-white/10 bg-white/[0.04]" />
          <div className="h-40 animate-pulse rounded-2xl border border-white/10 bg-white/[0.04]" />
          <div className="h-40 animate-pulse rounded-2xl border border-white/10 bg-white/[0.04]" />
        </div>
      </div>
    </LoadingBody>
  );
}
