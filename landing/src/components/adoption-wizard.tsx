"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { AgentSetupForm } from "@/components/agent-setup-form";

type WizardContextValue = {
  openWizard: () => void;
  closeWizard: () => void;
};

const WizardContext = createContext<WizardContextValue | undefined>(undefined);

export function useAdoptionWizard(): WizardContextValue {
  const ctx = useContext(WizardContext);
  if (!ctx) {
    throw new Error("useAdoptionWizard must be used within AdoptionWizardProvider");
  }
  return ctx;
}

export function AdoptionWizardProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const openWizard = useCallback(() => setOpen(true), []);
  const closeWizard = useCallback(() => setOpen(false), []);

  /** Recover from HMR / hard refresh leaving the page non-scrollable. */
  useEffect(() => {
    document.body.classList.remove("overflow-hidden");
    return () => document.body.classList.remove("overflow-hidden");
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeWizard();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, closeWizard]);

  useEffect(() => {
    if (open) document.body.classList.add("overflow-hidden");
    else document.body.classList.remove("overflow-hidden");
    return () => document.body.classList.remove("overflow-hidden");
  }, [open]);

  return (
    <WizardContext.Provider value={{ openWizard, closeWizard }}>
      {children}
      {open ? (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
          role="dialog"
          aria-modal="true"
          aria-labelledby="adoption-wizard-title"
        >
          <button
            type="button"
            className="absolute inset-0 bg-black/85 backdrop-blur-md transition-opacity"
            aria-label="Close"
            onClick={closeWizard}
          />
          <div
            className="relative z-10 w-full max-w-4xl max-h-[min(94vh,980px)] rounded-[1.75rem] bg-gradient-to-br from-aqua/25 via-lilac/15 to-coral/20 p-[1px] shadow-[0_0_60px_rgba(46,230,214,0.12)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex max-h-[min(94vh,978px)] flex-col overflow-hidden rounded-[1.7rem] bg-[#050810] ring-1 ring-white/10">
              <header className="relative shrink-0 overflow-hidden border-b border-white/10 px-6 py-6 sm:px-10 sm:py-8">
                <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_90%_80%_at_20%_-20%,rgba(46,230,214,0.12),transparent_50%)]" />
                <div className="relative flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0 max-w-2xl">
                    <p className="inline-flex rounded-full border border-aqua/30 bg-aqua/10 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.2em] text-aqua/95">
                      Self-serve
                    </p>
                    <h2
                      id="adoption-wizard-title"
                      className="font-display mt-4 text-2xl font-bold tracking-tight text-white sm:text-3xl md:text-[2rem]"
                    >
                      Adoption wizard
                    </h2>
                    <p className="mt-3 text-sm leading-relaxed text-white/60 sm:text-base">
                      A few choices about how you ship — then a single prompt you can paste into any coding agent. Nothing
                      is uploaded; everything stays in your browser until you copy.
                    </p>
                  </div>
                  <button
                    type="button"
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-white/15 bg-white/[0.04] text-lg leading-none text-white/75 transition hover:border-white/30 hover:bg-white/[0.08] hover:text-white"
                    onClick={closeWizard}
                    aria-label="Close"
                  >
                    ×
                  </button>
                </div>
              </header>
              <div className="min-h-0 flex-1 overflow-y-auto bg-gradient-to-b from-black/20 to-transparent px-6 pb-8 pt-2 sm:px-10 sm:pb-10">
                <AgentSetupForm />
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </WizardContext.Provider>
  );
}

export function AdoptionWizardButton({
  className,
  children = "Adoption wizard",
}: {
  className?: string;
  children?: ReactNode;
}) {
  const ctx = useContext(WizardContext);
  return (
    <button type="button" className={className} onClick={() => ctx?.openWizard()}>
      {children}
    </button>
  );
}
