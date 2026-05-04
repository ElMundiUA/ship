"use client";

/**
 * Expandable payload cell for the audit-log table (D12).
 *
 * The table lists ~50 rows at a time and most payloads are tiny
 * (email + role, token scopes, etc.), so we show the same truncated
 * one-liner that used to live inline. When an operator needs to dig
 * deeper — say, to check the exact `catalog_sources` shape on a
 * `workspace.update` row during an incident — they click "Expand" and
 * we reveal the full JSON verbatim, pretty-printed.
 *
 * We deliberately don't use any portal / modal: compliance reviews
 * tend to be "scan many rows, expand a couple". Inline reveal keeps
 * scroll position anchored and plays nicely with keyboard users
 * (the `<details>` element is fully native).
 */

import { useMemo } from "react";

interface Props {
  payload: Record<string, unknown>;
}

/**
 * One-liner preview: "key=value · key=value +N" (values truncated
 * to 32 chars). Matches the pre-D12 rendering so the default look
 * doesn't regress.
 */
function preview(payload: Record<string, unknown>): string {
  const keys = Object.keys(payload);
  if (keys.length === 0) return "—";
  const slice = keys.slice(0, 4).map((k) => {
    const v = payload[k];
    const s =
      typeof v === "string"
        ? v.length > 32
          ? `${v.slice(0, 32)}…`
          : v
        : JSON.stringify(v);
    return `${k}=${s}`;
  });
  const more = keys.length > 4 ? ` +${keys.length - 4}` : "";
  return slice.join(" · ") + more;
}

export function AuditPayloadCell({ payload }: Props) {
  const summary = useMemo(() => preview(payload), [payload]);
  const pretty = useMemo(() => JSON.stringify(payload, null, 2), [payload]);
  const isEmpty = Object.keys(payload).length === 0;

  if (isEmpty) {
    return <span className="text-xs text-white/40">—</span>;
  }

  return (
    <details className="group max-w-[48ch]">
      <summary
        className="flex cursor-pointer items-center gap-2 truncate font-mono text-[11px] text-white/55 transition hover:text-white/80"
        title="Click to reveal full JSON payload"
      >
        <span
          aria-hidden
          className="inline-block h-2 w-2 rotate-0 border-l border-b border-white/40 transition group-open:rotate-[-135deg]"
        />
        <code className="truncate">{summary}</code>
      </summary>
      <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-white/10 bg-black/40 p-3 text-[11px] leading-snug text-white/80">
        {pretty}
      </pre>
    </details>
  );
}
