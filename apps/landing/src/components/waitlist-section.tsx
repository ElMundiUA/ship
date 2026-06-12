import { WaitlistForm } from "./waitlist-form";

interface BetaCapacity {
  accepted: number;
  cap: number;
  full: boolean;
}

async function fetchBetaCapacity(): Promise<BetaCapacity | null> {
  try {
    const backendUrl = process.env.BACKEND_URL || "http://localhost:8100";
    const response = await fetch(`${backendUrl}/v1/public/beta-capacity`, {
      next: { revalidate: 300 }, // Cache for 5 minutes
    });

    if (!response.ok) {
      return null;
    }

    return response.json();
  } catch (error) {
    console.error("Failed to fetch beta capacity:", error);
    return null;
  }
}

type WaitlistSectionProps = {
  /** Email-only on homepage; full form on /beta */
  variant?: "full" | "email-only";
};

export async function WaitlistSection({ variant = "full" }: WaitlistSectionProps) {
  const capacity = await fetchBetaCapacity();

  return (
    <div className="space-y-6">
      {capacity ? (
        <>
          {variant === "full" ? (
            <div className="text-center">
              <p className="text-sm font-semibold text-aqua">
                {capacity.accepted} / {capacity.cap} closed-beta seats taken
              </p>
            </div>
          ) : null}

          {capacity.full ? (
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 sm:p-8">
              <p className="text-center text-lg font-semibold text-white">
                Waitlist full. We&apos;ll reopen for the next cohort.
              </p>
            </div>
          ) : (
            <WaitlistForm variant={variant} />
          )}
        </>
      ) : (
        <>
          {variant === "full" ? (
            <div className="text-center">
              <p className="text-sm font-semibold text-aqua">
                Closed beta access by invite — request below
              </p>
            </div>
          ) : null}
          <WaitlistForm variant={variant} />
        </>
      )}
    </div>
  );
}
