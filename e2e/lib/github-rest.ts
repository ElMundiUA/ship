/** Minimal GitHub REST helpers for e2e (issues + labels + comments). */

export const GH_API = "https://api.github.com";

export function ghHeaders(token: string) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

export function parseRepo(full: string): { owner: string; repo: string } | null {
  const i = full.indexOf("/");
  if (i <= 0 || i === full.length - 1) return null;
  return { owner: full.slice(0, i), repo: full.slice(i + 1) };
}
