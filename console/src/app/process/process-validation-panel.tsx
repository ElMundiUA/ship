"use client";

import type { ValidationItem, ValidationResult } from "./process-validation";

/**
 * Compact validation summary that lives under the Flow canvas. When
 * the result is clean (no errors, no warnings) the panel renders a
 * single green pill so the operator gets positive feedback that the
 * draft is good to publish; otherwise it lists each item with its
 * severity dot, message, and (when relevant) an anchor hint.
 *
 * The Publish button gates on ``errors.length === 0`` — the parent
 * editor passes the result here AND consults its `errors` array
 * directly to decide whether to disable the submit.
 */
export function ProcessValidationPanel({
  result,
  onJumpToStage,
  onJumpToTransition,
}: {
  result: ValidationResult;
  onJumpToStage?: (stageId: string) => void;
  onJumpToTransition?: (transitionId: string) => void;
}) {
  const { errors, warnings } = result;
  const isClean = errors.length === 0 && warnings.length === 0;

  if (isClean) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-emerald-400/30 bg-emerald-400/[0.05] px-3 py-2 text-xs">
        <span className="h-2 w-2 rounded-full bg-emerald-400" aria-hidden />
        <span className="font-semibold text-emerald-300">Process is valid</span>
        <span className="text-emerald-300/60">
          — every stage is reachable, every path terminates, all states are
          canonical.
        </span>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/55">
          Validation
        </div>
        <div className="flex items-center gap-2 text-[11px] font-semibold">
          {errors.length > 0 && (
            <span className="rounded-full border border-coral/30 bg-coral/[0.08] px-2 py-0.5 text-coral">
              {errors.length} error{errors.length === 1 ? "" : "s"}
            </span>
          )}
          {warnings.length > 0 && (
            <span className="rounded-full border border-amber-300/30 bg-amber-300/[0.08] px-2 py-0.5 text-amber-200">
              {warnings.length} warning{warnings.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
      </div>

      <ul className="mt-3 space-y-1.5">
        {errors.map((item) => (
          <ValidationRow
            key={`err-${item.code}-${item.anchor?.id ?? ""}`}
            item={item}
            onJumpToStage={onJumpToStage}
            onJumpToTransition={onJumpToTransition}
          />
        ))}
        {warnings.map((item) => (
          <ValidationRow
            key={`warn-${item.code}-${item.anchor?.id ?? ""}`}
            item={item}
            onJumpToStage={onJumpToStage}
            onJumpToTransition={onJumpToTransition}
          />
        ))}
      </ul>

      {errors.length > 0 && (
        <p className="mt-3 text-[11px] italic text-white/45">
          Publish is disabled until every error is resolved. Warnings are
          informational — the editor will still let you save.
        </p>
      )}
    </div>
  );
}

function ValidationRow({
  item,
  onJumpToStage,
  onJumpToTransition,
}: {
  item: ValidationItem;
  onJumpToStage?: (stageId: string) => void;
  onJumpToTransition?: (transitionId: string) => void;
}) {
  const isError = item.kind === "error";
  return (
    <li
      className={[
        "flex items-start gap-2.5 rounded-lg border px-3 py-2 text-xs",
        isError
          ? "border-coral/25 bg-coral/[0.04] text-coral/95"
          : "border-amber-300/25 bg-amber-300/[0.04] text-amber-100/90",
      ].join(" ")}
    >
      <span
        className={[
          "mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full",
          isError ? "bg-coral" : "bg-amber-300",
        ].join(" ")}
        aria-hidden
      />
      <span className="min-w-0 flex-1 leading-snug">{item.message}</span>
      {item.anchor && (item.anchor.kind === "stage"
        ? onJumpToStage
        : onJumpToTransition) ? (
        <button
          type="button"
          onClick={() => {
            if (!item.anchor) return;
            if (item.anchor.kind === "stage" && onJumpToStage) {
              onJumpToStage(item.anchor.id);
            }
            if (item.anchor.kind === "transition" && onJumpToTransition) {
              onJumpToTransition(item.anchor.id);
            }
          }}
          className={[
            "shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest transition",
            isError
              ? "border-coral/30 text-coral hover:bg-coral/[0.1]"
              : "border-amber-300/30 text-amber-200 hover:bg-amber-300/[0.1]",
          ].join(" ")}
        >
          Jump
        </button>
      ) : null}
    </li>
  );
}
