import { LoadingBody, SkeletonRows } from "@/components/loading-shell";

export default function Loading() {
  return (
    <LoadingBody>
      <div className="space-y-4">
        <div className="h-12 w-full animate-pulse rounded-xl border border-white/10 bg-white/[0.04]" />
        <SkeletonRows rows={5} />
      </div>
    </LoadingBody>
  );
}
