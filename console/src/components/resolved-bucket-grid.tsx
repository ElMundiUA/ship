import Link from "next/link";

import { Badge, Card } from "@/components/ui";
import type { ApiResolvedBucket } from "@/lib/api/types";
import { relativeTime } from "@/lib/mock/cloud";

/**
 * Shared grid of resolved knowledge buckets. Used by:
 *
 * - workspace-scoped ``/knowledge`` — non-workspace scope branch.
 * - repo-scoped ``/r/[owner]/[repo]/knowledge`` — only resolver
 *   output is rendered (no legacy ``.ship/knowledge`` list); the
 *   grid is the whole page.
 *
 * Kept scope-aware for empty-state copy (repo vs user) but does no
 * fetching itself — the caller passes the effective-filtered list.
 */
export function ResolvedBucketGrid({
  buckets,
  scopeKind,
}: {
  buckets: ApiResolvedBucket[];
  scopeKind: "workspace" | "repo" | "user";
}) {
  if (buckets.length === 0) {
    return (
      <Card className="text-center">
        <p className="text-sm text-white/70">
          No buckets visible in this scope yet.{" "}
          {scopeKind === "repo"
            ? "Push some .ship/knowledge/*.md files to this repo, or open Navigator and pack a conversation into a repo bucket."
            : "Pack a Navigator conversation into your personal bucket to populate this view."}{" "}
        </p>
        <div className="mt-4 flex justify-center gap-2">
          <Link
            href="/chat"
            className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-bold text-aqua hover:bg-aqua/20"
          >
            Open Navigator →
          </Link>
        </div>
      </Card>
    );
  }
  return (
    <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {buckets.map((b) => (
        <Card key={b.id} className="flex flex-col" data-testid="resolved-bucket">
          <div className="flex items-start justify-between gap-3">
            <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-lilac/40 via-aqua/30 to-coral/30 text-xl font-bold text-white">
              {emojiFor(b.slug)}
            </div>
            <div className="flex flex-col items-end gap-1">
              <Badge tone={scopeTone(b.scope_kind)}>{b.scope_kind}</Badge>
              <Badge tone="neutral">{b.source_kind.replace("_", " ")}</Badge>
            </div>
          </div>
          <h3 className="mt-3 font-display text-base font-bold text-white">
            {b.name}
          </h3>
          <p className="mt-1 line-clamp-3 text-xs text-white/60">
            {b.description || "No description."}
          </p>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-[11px]">
            <Stat k="Articles" v={b.summary_count.toString()} />
            <Stat k="Updated" v={relativeTime(b.updated_at)} />
          </dl>
          <div className="mt-auto pt-4">
            <Link
              href={`/knowledge/${encodeURIComponent(b.slug)}`}
              className="font-semibold text-aqua hover:underline"
            >
              Open →
            </Link>
          </div>
        </Card>
      ))}
    </section>
  );
}

function scopeTone(
  kind: ApiResolvedBucket["scope_kind"],
): "workspace" | "project" | "ok" | "warn" {
  if (kind === "workspace") return "workspace";
  if (kind === "project") return "project";
  if (kind === "user") return "warn";
  return "ok";
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded-md border border-white/10 bg-white/[0.02] px-2 py-1.5">
      <div className="text-[9px] font-bold uppercase tracking-widest text-white/40">
        {k}
      </div>
      <div className="mt-0.5 font-semibold text-white">{v}</div>
    </div>
  );
}

function emojiFor(slug: string): string {
  const lower = slug.toLowerCase();
  if (lower.includes("brand")) return "✦";
  if (lower.includes("style") || lower.includes("code")) return "⌘";
  if (lower.includes("test")) return "✓";
  if (lower.includes("devops") || lower.includes("ops")) return "⚙";
  if (lower.includes("security")) return "🛡";
  if (lower.includes("design")) return "✦";
  if (lower.includes("compliance")) return "§";
  return "◆";
}
