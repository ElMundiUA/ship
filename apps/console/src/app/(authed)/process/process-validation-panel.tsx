"use client";

import type { ValidationItem, ValidationResult } from "./process-validation";

/**
 * Compact validation pill that lives sticky at the bottom-right of
 * the Flow canvas. Renders a quiet ``X errors · Y warnings`` summary
 * with an expandable list — clicking a row jumps the editor to the
 * offending stage or transition. Hidden entirely on a clean draft so
 * the canvas stays quiet when there's nothing to do.
 *
 * The Publish button gates on ``errors.length === 0`` directly from
 * the parent editor, not via the panel.
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
  if (errors.length === 0 && warnings.length === 0) return null;

  return (
    <details className="group max-w-md rounded-lg bg-ink/95 backdrop-blur ring-1 ring-white/10 transition open:bg-ink">
      <summary className="cursor-pointer select-none list-none px-3 py-2 text-[11px] font-semibold">
        <span className="inline-flex items-center gap-3">
          {errors.length > 0 && (
            <span className="text-coral">
              {errors.length} error{errors.length === 1 ? "" : "s"}
            </span>
          )}
          {warnings.length > 0 && (
            <span className="text-sun">
              {warnings.length} warning{warnings.length === 1 ? "" : "s"}
            </span>
          )}
          <span className="text-white/40 transition group-open:rotate-90">
            ›
          </span>
        </span>
      </summary>
      <ul className="space-y-1 px-3 pb-3">
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
    </details>
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
    <li className="flex items-start gap-2 py-1 text-[11px] leading-snug">
      <span
        aria-hidden
        className={[
          "mt-1 h-1.5 w-1.5 shrink-0 rounded-full",
          isError ? "bg-coral" : "bg-sun",
        ].join(" ")}
      />
      <span
        className={[
          "min-w-0 flex-1",
          isError ? "text-coral/90" : "text-sun/90",
        ].join(" ")}
      >
        {item.message}
      </span>
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
            "shrink-0 text-[10px] font-bold uppercase tracking-widest transition",
            isError
              ? "text-coral hover:text-white"
              : "text-sun hover:text-white",
          ].join(" ")}
        >
          Jump
        </button>
      ) : null}
    </li>
  );
}
