import { LoadingShell, SkeletonRows } from "@/components/loading-shell";

export default function Loading() {
  return (
    <LoadingShell>
      <div className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="h-7 w-20 animate-pulse rounded-full bg-white/[0.06]"
            />
          ))}
        </div>
        <SkeletonRows rows={6} />
      </div>
    </LoadingShell>
  );
}
