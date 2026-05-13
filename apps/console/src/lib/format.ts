/**
 * Tiny presentation helpers shared across console pages.
 *
 * Lives outside ``lib/mock/`` because production routes pull these in too —
 * the mock module's import-time guard would otherwise blow up the prod build.
 */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso).getTime();
  let diff = (now.getTime() - then) / 1000;
  if (diff < 0) diff = 0;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86_400) return `${Math.round(diff / 3600)}h ago`;
  if (diff < 86_400 * 30) return `${Math.round(diff / 86_400)}d ago`;
  return new Date(iso).toLocaleDateString();
}
