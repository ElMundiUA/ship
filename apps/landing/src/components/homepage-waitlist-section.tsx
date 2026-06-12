import { WaitlistSection } from "@/components/waitlist-section";

export function HomepageWaitlistSection() {
  return (
    <section
      id="waitlist"
      aria-labelledby="waitlist-heading"
      className="py-20 sm:py-24"
    >
      <div className="mx-auto max-w-xl px-4 sm:px-6">
        <div className="text-center">
          <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">Early access</p>
          <h2
            id="waitlist-heading"
            className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl"
          >
            Join the waitlist
          </h2>
          <p className="mt-4 text-lg text-white/65">
            Leave your email — we&apos;ll reach out when the next cohort opens. Want to share more
            context now?{" "}
            <a href="/beta" className="text-sun hover:text-white">
              Use the full beta form
            </a>
            .
          </p>
        </div>
        <div className="mt-10">
          <WaitlistSection variant="email-only" />
        </div>
      </div>
    </section>
  );
}
