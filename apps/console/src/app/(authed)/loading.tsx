import { LoadingBody } from "@/components/loading-shell";

/**
 * Fallback loading state for any (authed) route that does not ship its
 * own loading.tsx (e.g. /repos/[id]/secrets, /chat/archived). Renders
 * inside the layout's already-mounted AppShellChrome — must NOT paint
 * its own sidebar, or we get the double-chrome glitch.
 */
export default function Loading() {
  return <LoadingBody />;
}
