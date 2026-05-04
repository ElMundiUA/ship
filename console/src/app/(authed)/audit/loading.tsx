import { LoadingBody, SkeletonRows } from "@/components/loading-shell";

export default function Loading() {
  return (
    <LoadingBody>
      <SkeletonRows rows={8} />
    </LoadingBody>
  );
}
