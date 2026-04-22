"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { repoBasePath } from "@/lib/repo-slug";
import type {
  ApiPolicy,
  ApiPolicyRepoCompliance,
} from "@/lib/api/client";
import {
  Badge,
  ButtonDanger,
  ButtonGhost,
  Card,
  CardHeader,
  StatTile,
} from "@/components/ui";

/**
 * Client island for the Policy list.
 *
 * Keeps the per-policy compliance tiles + repo rows interactive
 * (opt-out toggle, policy delete) without pulling the whole page
 * into a client component. Optimistic updates: the server response
 * after a POST/DELETE returns the full ``ApiPolicy`` including the
 * recomputed compliance rollup, so we swap it in place rather than
 * refetching the list.
 */
export function PoliciesList({
  workspaceId,
  policies: initial,
}: {
  workspaceId: string;
  policies: ApiPolicy[];
}) {
  const [policies, setPolicies] = useState<ApiPolicy[]>(initial);

  function replace(updated: ApiPolicy) {
    setPolicies((prev) =>
      prev.map((p) => (p.id === updated.id ? updated : p)),
    );
  }

  function removeLocal(id: string) {
    setPolicies((prev) => prev.filter((p) => p.id !== id));
  }

  return (
    <div className="flex flex-col gap-4">
      {policies.map((policy) => (
        <PolicyCard
          key={policy.id}
          policy={policy}
          workspaceId={workspaceId}
          onReplace={replace}
          onRemove={() => removeLocal(policy.id)}
        />
      ))}
    </div>
  );
}

function PolicyCard({
  policy,
  workspaceId,
  onReplace,
  onRemove,
}: {
  policy: ApiPolicy;
  workspaceId: string;
  onReplace: (p: ApiPolicy) => void;
  onRemove: () => void;
}) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  async function deletePolicy() {
    if (
      !window.confirm(
        `Delete policy "${policy.name}"? This doesn't remove already-wired lanes on repos.`,
      )
    ) {
      return;
    }
    startTransition(async () => {
      setError(null);
      try {
        const res = await fetch(
          `/api/policies/${policy.id}?workspaceId=${encodeURIComponent(workspaceId)}`,
          { method: "DELETE" },
        );
        if (!res.ok && res.status !== 204) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body?.error ?? `HTTP ${res.status}`);
        }
        onRemove();
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to delete");
      }
    });
  }

  async function toggleException(
    repo: ApiPolicyRepoCompliance,
    shouldExcept: boolean,
  ) {
    startTransition(async () => {
      setError(null);
      try {
        const url = `/api/policies/${policy.id}/exceptions/${repo.repo_id}`;
        const res = shouldExcept
          ? await fetch(url, {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ workspaceId }),
            })
          : await fetch(
              `${url}?workspaceId=${encodeURIComponent(workspaceId)}`,
              { method: "DELETE" },
            );
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body?.error ?? `HTTP ${res.status}`);
        }
        const updated = (await res.json()) as ApiPolicy;
        onReplace(updated);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed");
      }
    });
  }

  const { compliance } = policy;
  const percent =
    compliance.total_repos > 0
      ? Math.round(
          (compliance.compliant / compliance.total_repos) * 100,
        )
      : 0;

  return (
    <Card padded={false}>
      <div className="flex flex-wrap items-start gap-4 border-b border-white/[0.08] px-5 py-4">
        <div className="min-w-0 flex-1">
          <CardHeader
            title={policy.name}
            subtitle={
              <span className="font-mono text-[11px]">
                {policy.pattern_id} · lane{" "}
                <span className="text-white/80">{policy.lane_id}</span> ·{" "}
                {policy.cadence}
                {policy.agent_slug ? ` · agent ${policy.agent_slug}` : null}
              </span>
            }
          />
          {!policy.enabled ? (
            <div className="mt-2">
              <Badge tone="warn">disabled</Badge>
            </div>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <ButtonGhost onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Hide repos" : "Show repos"}
          </ButtonGhost>
          <ButtonDanger onClick={deletePolicy}>
            {pending ? "…" : "Delete"}
          </ButtonDanger>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 px-5 py-4 md:grid-cols-4">
        <StatTile
          label="Repos"
          value={String(compliance.total_repos)}
          hint="Total activated"
        />
        <StatTile
          label="Compliant"
          value={String(compliance.compliant)}
          hint={
            compliance.total_repos > 0
              ? `${percent}% of fleet`
              : "No repos yet"
          }
        />
        <StatTile
          label="Missing"
          value={String(compliance.missing)}
          hint="Lane not wired"
        />
        <StatTile
          label="Excepted"
          value={String(compliance.excepted)}
          hint="Opted out"
        />
      </div>

      {error ? (
        <div className="border-t border-white/[0.08] bg-rose-500/10 px-5 py-2 text-xs text-rose-200">
          {error}
        </div>
      ) : null}

      {expanded ? (
        <div className="border-t border-white/[0.08]">
          <div className="px-5 py-3 text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
            Per-repo status
          </div>
          <ul className="divide-y divide-white/[0.06]">
            {compliance.repos.map((repo) => (
              <li
                key={repo.repo_id}
                className="flex flex-wrap items-center gap-3 px-5 py-3"
              >
                <Link
                  href={repoBasePath({ full_name: repo.full_name })}
                  className="min-w-0 flex-1 font-mono text-sm text-white/90 hover:text-white"
                >
                  {repo.full_name}
                </Link>
                <Badge tone={statusTone(repo.status)} dot>
                  {repo.status}
                </Badge>
                {repo.exception_reason ? (
                  <span className="text-[11px] text-white/50">
                    {repo.exception_reason}
                  </span>
                ) : null}
                {repo.status === "excepted" ? (
                  <ButtonGhost onClick={() => toggleException(repo, false)}>
                    Remove opt-out
                  </ButtonGhost>
                ) : (
                  <ButtonGhost onClick={() => toggleException(repo, true)}>
                    Opt out
                  </ButtonGhost>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

function statusTone(
  status: ApiPolicyRepoCompliance["status"],
): "ok" | "warn" | "neutral" {
  switch (status) {
    case "compliant":
      return "ok";
    case "missing":
      return "warn";
    case "excepted":
      return "neutral";
  }
}
