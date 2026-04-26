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

  return (
    <label className="block rounded-2xl border border-white/10 bg-white/[0.035] px-3 py-2">
      <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/40">
        Repository process
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
        className="w-full rounded-xl border border-white/10 bg-ink px-3 py-2 text-sm font-semibold text-white outline-none focus:border-aqua/40"
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
