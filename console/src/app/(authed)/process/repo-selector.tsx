"use client";

import { useRouter, useSearchParams } from "next/navigation";

import type { ApiActivatedRepo } from "@/lib/api/client";

export function RepoSelector({
  repos,
  selectedRepo,
  processId,
}: {
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
  processId?: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();

  if (repos.length === 0) {
    return (
      <div className="rounded-2xl border border-coral/25 bg-coral/[0.05] px-4 py-3 text-sm text-coral/90">
        No repository selected. Activate a repository in onboarding or Settings
        before editing a repo process.
      </div>
    );
  }

  // Hide the picker entirely when there's only one activated repo —
  // the dropdown does nothing useful in that case and just adds chrome.
  if (repos.length === 1) return null;

  return (
    <label className="inline-flex items-center gap-1.5 text-[11px] text-white/55">
      <span className="text-[10px] font-bold uppercase tracking-widest text-white/40">
        Repo
      </span>
      <select
        value={selectedRepo?.id ?? repos[0]?.id}
        onChange={(event) => {
          const next = new URLSearchParams(searchParams.toString());
          next.set("repo", event.currentTarget.value);
          next.delete("state");
          const base = processId
            ? `/process/${encodeURIComponent(processId)}`
            : "/process";
          router.push(`${base}?${next.toString()}`);
        }}
        className="rounded-md border border-white/10 bg-ink px-2 py-1 text-[12px] font-semibold text-white outline-none focus:border-aqua/40"
      >
        {repos.map((repo) => (
          <option key={repo.id} value={repo.id}>
            {repo.full_name}
          </option>
        ))}
      </select>
    </label>
  );
}
