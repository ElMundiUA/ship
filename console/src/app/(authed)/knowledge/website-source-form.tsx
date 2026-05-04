"use client";

/**
 * WebsiteSourceForm — URL field + "Preview crawl" probe + advanced
 * scrape options (limit, subdomains, change-tracking, only-main-content).
 *
 * Without the preview, operators created a website source and discovered
 * the crawl scope only after the first sync. This component runs
 * Firecrawl /map ahead of submit so the operator confirms the URL set
 * and tunes the limit before paying for the scrape budget.
 */

import { useState } from "react";

export type WebsiteConfig = {
  url: string;
  limit: number;
  include_subdomains: boolean;
  change_tracking: boolean;
  only_main_content: boolean;
};

export const DEFAULT_WEBSITE_CONFIG: WebsiteConfig = {
  url: "",
  limit: 25,
  include_subdomains: false,
  change_tracking: true,
  only_main_content: true,
};

type PreviewState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; urls: string[]; truncated: boolean }
  | { kind: "error"; message: string };

export function WebsiteSourceForm({
  workspaceId,
  config,
  onChange,
}: {
  workspaceId: string | undefined;
  config: WebsiteConfig;
  onChange: (next: WebsiteConfig) => void;
}) {
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });
  const [showAdvanced, setShowAdvanced] = useState(false);

  function update<K extends keyof WebsiteConfig>(key: K, value: WebsiteConfig[K]) {
    onChange({ ...config, [key]: value });
  }

  async function runPreview() {
    if (!workspaceId || !config.url.trim()) return;
    setPreview({ kind: "loading" });
    try {
      const params = new URLSearchParams({
        workspaceId,
        url: config.url.trim(),
        limit: String(config.limit),
      });
      if (config.include_subdomains) params.set("includeSubdomains", "true");
      const resp = await fetch(`/api/knowledge/website-preview?${params.toString()}`, {
        method: "GET",
      });
      if (!resp.ok) {
        const payload = await resp.json().catch(() => ({}));
        const message =
          typeof payload?.error === "string" ? payload.error : `HTTP ${resp.status}`;
        setPreview({ kind: "error", message });
        return;
      }
      const data = (await resp.json()) as { urls: string[]; truncated: boolean };
      setPreview({ kind: "ready", urls: data.urls, truncated: data.truncated });
    } catch (err) {
      setPreview({
        kind: "error",
        message: err instanceof Error ? err.message : "Preview failed",
      });
    }
  }

  return (
    <div className="space-y-3" data-testid="website-source-form">
      <div className="flex gap-2">
        <input
          value={config.url}
          onChange={(event) => update("url", event.target.value)}
          placeholder="https://docs.example.com"
          className="flex-1 rounded border border-white/15 bg-black/30 px-3 py-2 text-sm text-white/90"
          aria-label="Website URL"
        />
        <button
          type="button"
          onClick={runPreview}
          disabled={!config.url.trim() || !workspaceId || preview.kind === "loading"}
          className="rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {preview.kind === "loading" ? "Mapping…" : "Preview crawl"}
        </button>
      </div>

      <details
        open={showAdvanced}
        onToggle={(event) => setShowAdvanced((event.target as HTMLDetailsElement).open)}
        className="rounded border border-white/10 bg-white/[0.02] px-3 py-2"
      >
        <summary className="cursor-pointer text-[11px] uppercase tracking-wider text-white/55">
          Advanced options
        </summary>
        <div className="mt-3 space-y-2 text-xs text-white/80">
          <label className="flex items-center justify-between gap-3">
            <span>Page limit (max URLs scraped)</span>
            <input
              type="number"
              min={1}
              max={200}
              value={config.limit}
              onChange={(event) => update("limit", Number(event.target.value) || 25)}
              className="w-20 rounded border border-white/15 bg-black/30 px-2 py-1 text-right text-white/90"
            />
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={config.include_subdomains}
              onChange={(event) => update("include_subdomains", event.target.checked)}
              className="accent-aqua"
            />
            Include subdomains
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={config.only_main_content}
              onChange={(event) => update("only_main_content", event.target.checked)}
              className="accent-aqua"
            />
            Strip nav / footer (only-main-content mode)
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={config.change_tracking}
              onChange={(event) => update("change_tracking", event.target.checked)}
              className="accent-aqua"
            />
            Track diffs across syncs (skips unchanged pages)
          </label>
        </div>
      </details>

      {preview.kind === "loading" && (
        <p className="text-xs text-white/55">Mapping the site via Firecrawl…</p>
      )}
      {preview.kind === "error" && (
        <p className="text-xs text-coral">{preview.message}</p>
      )}
      {preview.kind === "ready" && (
        <div className="rounded border border-white/10 bg-black/30">
          <div className="border-b border-white/5 px-3 py-2 text-[11px] uppercase tracking-wider text-white/55">
            {preview.urls.length} URL{preview.urls.length === 1 ? "" : "s"}
            {preview.truncated && " (more available — bump the limit)"}
          </div>
          {preview.urls.length === 0 ? (
            <p className="px-3 py-3 text-xs text-white/55">
              Firecrawl found nothing reachable from this URL. Check the URL or whether it&apos;s behind auth.
            </p>
          ) : (
            <ul className="max-h-48 divide-y divide-white/5 overflow-y-auto text-xs">
              {preview.urls.map((url) => (
                <li key={url} className="truncate px-3 py-1.5 text-white/75">
                  {url}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <p className="text-[11px] text-white/45">
        Firecrawl maps URLs from the root, scrapes Markdown, and skips unchanged pages by hash on subsequent syncs.
      </p>
    </div>
  );
}
