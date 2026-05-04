import { LoadingShell, SkeletonRows } from "@/components/loading-shell";

export default function Loading() {
  return (
    <LoadingShell>
      <SkeletonRows rows={8} />
    </LoadingShell>
  );
}
