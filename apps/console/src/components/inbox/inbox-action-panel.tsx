"use client";

/**
 * Inbox Decision UI (ELS-160) — single panel that renders the four
 * resolution modes off the row's `payload.action_items` + the
 * server-derived `resolution_mode` hint:
 *
 *   - `single_choice`  → row of pill buttons (1 click resolves).
 *   - `multi_select`   → checkbox list + Apply N selected.
 *   - `ack_only`       → one Acknowledge button.
 *   - `freeform_only`  → collapsed one-line input (expand → textarea).
 *
 * Under every mode there's a collapsed-by-default "▸ Or answer in
 * your own words" line that expands into a single-line input;
 * pressing ⏎ or clicking outside collapses. Selecting pills /
 * checkboxes + adding freeform is allowed — both reach the backend
 * in one /decide call.
 *
 * Replaces the row-action buttons + the textarea quick-reply path
 * inside `inbox-row-actions.tsx` + `mailbox-footer.tsx`. Those files
 * stay (other surfaces depend on them) but their interactive zones
 * are now subsets of what this component offers.
 */

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import { ButtonPrimary } from "@/components/ui";
import { decideInboxItemClient } from "@/lib/inbox-decide-client";
import type {
  InboxActionItem,
  InboxItemDetail,
  InboxResolutionMode,
} from "@/lib/inbox-types";
import { reportActionItemDecisions } from "@/lib/inbox-types";

// ``InboxActionPanel`` is a client component but the underlying
// ``decideInboxItem`` helper lives in ``@/lib/api/client`` which is
// tagged ``import "server-only"`` so the session token can't leak to
// the browser bundle. Pre-fix the panel imported it directly and the
// Next.js build failed with "You're importing a component that needs
// server-only" — every backend deploy was blocked since 2026-05-18
// because the console image refused to build. Call the server-side
// bridge at ``/api/inbox/[id]/decide`` instead; the route handler is
// server code and forwards the call to the backend.

type Props = {
  workspaceId: string;
  item: InboxItemDetail;
  /**
   * Optional callback fired after a successful /decide round-trip.
   * Defaults to `router.refresh()` so the panel works standalone.
   */
  onResolved?: (next: InboxItemDetail) => void;
};

function readActionItems(
  payload: Record<string, unknown> | undefined,
): InboxActionItem[] {
  if (!payload || typeof payload !== "object") return [];
  const raw = (payload as Record<string, unknown>)["action_items"];
  if (!Array.isArray(raw)) return [];
  return raw
    .filter(
      (x): x is InboxActionItem =>
        typeof x === "object" &&
        x !== null &&
        typeof (x as { id?: unknown }).id === "string" &&
        typeof (x as { label?: unknown }).label === "string" &&
        typeof (x as { kind?: unknown }).kind === "string",
    )
    .map((raw) => ({
      id: String(raw.id),
      kind: raw.kind,
      label: String(raw.label),
      hint: raw.hint ?? null,
      secondary_label: raw.secondary_label ?? null,
      default: !!raw.default,
      target_project_id: raw.target_project_id ?? null,
    }));
}

export function InboxActionPanel({ workspaceId, item, onResolved }: Props) {
  const router = useRouter();
  const mode = (item.resolution_mode || "freeform_only") as InboxResolutionMode;
  const items = useMemo(() => readActionItems(item.payload), [item.payload]);
  const decisions = useMemo(
    () => reportActionItemDecisions(item.payload),
    [item.payload],
  );
  const [picked, setPicked] = useState<Set<string>>(
    () => new Set(items.filter((i) => i.default).map((i) => i.id)),
  );
  const [freeform, setFreeform] = useState("");
  const [freeformOpen, setFreeformOpen] = useState(mode === "freeform_only");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const freeformRef = useRef<HTMLInputElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  const isTerminal = item.status === "resolved" || item.status === "dismissed";

  const submit = async (selections: string[], freeformOverride?: string) => {
    if (busy || isTerminal) return;
    const text = (freeformOverride ?? freeform).trim();
    if (selections.length === 0 && !text) return;
    setBusy(true);
    setError(null);
    try {
      const next = await decideInboxItemClient(workspaceId, item.id, {
        selections,
        freeform: text || null,
      });
      if (onResolved) {
        onResolved(next);
      } else {
        router.refresh();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not apply decision.");
    } finally {
      setBusy(false);
    }
  };

  const submitBinary = async (
    actionItemId: string,
    choice: "primary" | "secondary",
  ) => {
    if (busy || isTerminal) return;
    setBusy(true);
    setError(null);
    try {
      const next = await decideInboxItemClient(workspaceId, item.id, {
        action_item_id: actionItemId,
        choice,
      });
      if (onResolved) {
        onResolved(next);
      } else {
        router.refresh();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not apply decision.");
    } finally {
      setBusy(false);
    }
  };

  // ELS-167 keyboard shortcuts:
  //   1-9 → pick / toggle the Nth visible item
  //   Space → toggle current checkbox (multi_select)
  //   Enter → Apply / Acknowledge / Send freeform
  //   e → open + focus freeform input
  //   Esc → blur freeform / collapse it
  //
  // We deliberately listen on `document` so the operator doesn't have
  // to click the panel first. Bail out when the focused element is an
  // editable surface ELSEWHERE on the page (e.g., a Linear textarea
  // inside the preview pane) — only the freeform input on this panel
  // is "ours".
  useEffect(() => {
    if (isTerminal) return;

    function shouldHijack(target: EventTarget | null): boolean {
      if (!(target instanceof HTMLElement)) return true;
      // Our own freeform input is intentional — Enter still submits.
      if (target === freeformRef.current) return true;
      // Any OTHER editable surface owns its keystrokes.
      const tag = target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
        return false;
      }
      if (target.isContentEditable) return false;
      return true;
    }

    function onKey(e: KeyboardEvent) {
      if (busy) return;
      if (!shouldHijack(e.target)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      // Freeform input case — only Enter submits, Esc collapses.
      if (e.target === freeformRef.current) {
        if (e.key === "Escape") {
          e.preventDefault();
          freeformRef.current?.blur();
          if (mode !== "freeform_only") setFreeformOpen(false);
        }
        return; // Enter is handled by the input's onKeyDown
      }

      // `e` → open freeform and focus it.
      if (e.key === "e" || e.key === "E") {
        e.preventDefault();
        setFreeformOpen(true);
        // Defer focus until input mounts after state flush.
        requestAnimationFrame(() => freeformRef.current?.focus());
        return;
      }

      // Digit picks the Nth visible action_item.
      if (e.key >= "1" && e.key <= "9") {
        const idx = parseInt(e.key, 10) - 1;
        if (idx < 0 || idx >= items.length) return;
        const ai = items[idx];
        if (mode === "single_choice" && ai.kind === "choice") {
          e.preventDefault();
          void submit([ai.id]);
        } else if (mode === "multi_select") {
          e.preventDefault();
          const next = new Set(picked);
          if (next.has(ai.id)) next.delete(ai.id);
          else next.add(ai.id);
          setPicked(next);
        }
        return;
      }

      // Enter → Apply / Acknowledge.
      if (e.key === "Enter") {
        e.preventDefault();
        if (mode === "multi_select") {
          if (picked.size > 0) void submit(Array.from(picked));
        } else if (mode === "ack_only") {
          void submit(items.length ? [items[0].id] : [], "");
        }
        return;
      }
    }

    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, isTerminal, items, mode, picked, submit]);

  return (
    <div ref={panelRef} className="space-y-3">
      {mode === "single_choice" && (
        <SingleChoiceRow
          items={items.filter((i) => i.kind === "choice")}
          disabled={busy || isTerminal}
          onPick={(id) => submit([id])}
        />
      )}

      {mode === "multi_select" && (
        <MultiSelectList
          items={items}
          picked={picked}
          setPicked={setPicked}
          disabled={busy || isTerminal}
          onApply={() => submit(Array.from(picked))}
        />
      )}

      {mode === "ack_only" && (
        <ButtonPrimary
          size="sm"
          type="button"
          onClick={() => submit(items.length ? [items[0].id] : [], "")}
          className={cn(
            (busy || isTerminal) && "pointer-events-none opacity-40",
          )}
        >
          {busy ? "…" : "Acknowledge"}
        </ButtonPrimary>
      )}

      {mode === "per_item_binary" && (
        <BinaryActionList
          items={items.filter((i) => i.kind === "binary")}
          decisions={decisions}
          disabled={busy || isTerminal}
          onPick={submitBinary}
        />
      )}

      {mode !== "per_item_binary" && (
      <FreeformRow
        open={freeformOpen}
        setOpen={setFreeformOpen}
        value={freeform}
        setValue={setFreeform}
        disabled={busy || isTerminal}
        onSubmit={() => submit(Array.from(picked), freeform)}
        forced={mode === "freeform_only"}
        inputRef={freeformRef}
      />
      )}

      {!isTerminal && (mode === "single_choice" || mode === "multi_select" || mode === "ack_only") && (
        <KeyboardHint mode={mode} />
      )}

      {error && (
        <p className="text-[11px] font-semibold text-coral">{error}</p>
      )}
    </div>
  );
}

function KeyboardHint({ mode }: { mode: InboxResolutionMode }) {
  const parts: string[] = [];
  if (mode === "single_choice") parts.push("1-9 pick");
  if (mode === "multi_select") parts.push("1-9 toggle", "⏎ Apply");
  if (mode === "ack_only") parts.push("⏎ Acknowledge");
  parts.push("e edit");
  return (
    <p className="text-[10px] font-mono uppercase tracking-wider text-white/30">
      {parts.join(" · ")}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Sub-renderers
// ---------------------------------------------------------------------------

function BinaryActionList({
  items,
  decisions,
  disabled,
  onPick,
}: {
  items: InboxActionItem[];
  decisions: Record<string, "primary" | "secondary">;
  disabled: boolean;
  onPick: (id: string, choice: "primary" | "secondary") => void;
}) {
  if (items.length === 0) {
    return (
      <p className="text-xs italic text-white/40">
        No recommendations on this digest.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {items.map((item) => {
        const decided = decisions[item.id];
        return (
          <div
            key={item.id}
            className="rounded border border-white/10 bg-white/[0.03] p-3"
          >
            {item.hint ? (
              <p className="text-sm text-white/85">{item.hint}</p>
            ) : null}
            {decided ? (
              <p className="mt-2 text-xs text-white/50">
                Decided:{" "}
                {decided === "primary"
                  ? item.label
                  : item.secondary_label || item.label}
              </p>
            ) : (
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onPick(item.id, "primary")}
                  className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.08] disabled:opacity-40"
                >
                  {item.label}
                </button>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onPick(item.id, "secondary")}
                  className="inline-flex items-center gap-1.5 rounded-full border border-coral/40 bg-coral/10 px-3 py-1.5 text-xs font-semibold text-coral transition hover:bg-coral/20 disabled:opacity-40"
                >
                  {item.secondary_label || "Decline"}
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function SingleChoiceRow({
  items,
  disabled,
  onPick,
}: {
  items: InboxActionItem[];
  disabled: boolean;
  onPick: (id: string) => void;
}) {
  if (items.length === 0) {
    return (
      <p className="text-xs italic text-white/40">
        No options — answer below.
      </p>
    );
  }
  return (
    // Stack choices vertically — long labels (e.g. "Closed PR,
    // commented recipe on Linear") rendered as a horizontal pill row
    // wrap awkwardly and force the operator to scan zig-zag across
    // the preview. One per row keeps scanning top-to-bottom and lets
    // the optional ``hint`` fit alongside the label on hover.
    <div className="flex flex-col gap-1.5">
      {items.map((i, idx) => (
        <button
          key={i.id}
          type="button"
          disabled={disabled}
          onClick={() => onPick(i.id)}
          title={i.hint || undefined}
          className="group inline-flex w-full items-center gap-2 rounded-md border border-white/15 bg-white/[0.04] px-3 py-2 text-left text-xs font-semibold text-white transition hover:border-aqua hover:bg-aqua/15 hover:text-aqua disabled:opacity-40"
        >
          {idx < 9 && (
            <span className="font-mono text-[9px] font-bold text-white/40 group-hover:text-aqua/70">
              {idx + 1}
            </span>
          )}
          <span className="flex-1">{i.label}</span>
        </button>
      ))}
    </div>
  );
}

function MultiSelectList({
  items,
  picked,
  setPicked,
  disabled,
  onApply,
}: {
  items: InboxActionItem[];
  picked: Set<string>;
  setPicked: (next: Set<string>) => void;
  disabled: boolean;
  onApply: () => void;
}) {
  if (items.length === 0) {
    return (
      <p className="text-xs italic text-white/40">
        No proposed actions — answer below.
      </p>
    );
  }
  const toggle = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  };
  return (
    <div className="space-y-2">
      <ul className="space-y-1">
        {items.map((i, idx) => (
          <li
            key={i.id}
            className={cn(
              "flex items-start gap-2 rounded-md border border-transparent px-2 py-1.5 transition",
              picked.has(i.id) && "border-white/10 bg-white/[0.04]",
            )}
          >
            <input
              id={`ai-${i.id}`}
              type="checkbox"
              checked={picked.has(i.id)}
              onChange={() => toggle(i.id)}
              disabled={disabled}
              className="mt-0.5 h-3.5 w-3.5 cursor-pointer accent-aqua disabled:cursor-not-allowed"
            />
            {idx < 9 && (
              <span className="mt-0.5 inline-block w-4 select-none font-mono text-[9px] font-bold text-white/30">
                {idx + 1}
              </span>
            )}
            <label
              htmlFor={`ai-${i.id}`}
              className={cn(
                "min-w-0 cursor-pointer text-sm leading-snug",
                disabled && "cursor-not-allowed",
              )}
            >
              <span className="font-medium text-white">{i.label}</span>
              {i.hint && (
                <span className="block text-[11px] text-white/55">
                  {i.hint}
                </span>
              )}
            </label>
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-2 pt-1">
        <ButtonPrimary
          size="sm"
          type="button"
          onClick={onApply}
          className={cn(
            (disabled || picked.size === 0) && "pointer-events-none opacity-40",
          )}
        >
          Apply {picked.size > 0 ? `${picked.size} selected` : ""}
        </ButtonPrimary>
        <span className="text-[11px] text-white/40">
          {picked.size === 0
            ? "Pick one or more, or write your own answer below."
            : `${picked.size} of ${items.length}`}
        </span>
      </div>
    </div>
  );
}

function FreeformRow({
  open,
  setOpen,
  value,
  setValue,
  disabled,
  onSubmit,
  forced,
  inputRef,
}: {
  open: boolean;
  setOpen: (v: boolean) => void;
  value: string;
  setValue: (v: string) => void;
  disabled: boolean;
  onSubmit: () => void;
  forced: boolean;
  inputRef?: React.MutableRefObject<HTMLInputElement | null>;
}) {
  if (!open && !forced) {
    return (
      <button
        type="button"
        onClick={() => {
          setOpen(true);
          requestAnimationFrame(() => inputRef?.current?.focus());
        }}
        className="text-[11px] font-semibold uppercase tracking-wide text-white/55 transition hover:text-white"
      >
        ▸ Or answer in your own words
      </button>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onSubmit();
          }
        }}
        disabled={disabled}
        placeholder="Type your answer and press Enter…"
        className="min-w-0 flex-1 rounded-md border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-sm text-white placeholder:text-white/30 focus:border-aqua focus:outline-none disabled:opacity-40"
      />
      <ButtonPrimary
        size="sm"
        type="button"
        onClick={onSubmit}
        className={cn(
          "uppercase tracking-wide",
          (disabled || value.trim() === "") && "pointer-events-none opacity-40",
        )}
      >
        Send
      </ButtonPrimary>
    </div>
  );
}
