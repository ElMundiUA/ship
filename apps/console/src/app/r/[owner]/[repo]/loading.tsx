import { LoadingShell, SkeletonRows } from "@/components/loading-shell";

export default function Loading() {
  return (
    <LoadingShell>
      <SkeletonRows rows={4} />
    </LoadingShell>
  );
}
