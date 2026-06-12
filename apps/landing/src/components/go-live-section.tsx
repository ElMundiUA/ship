import Link from "next/link";

export function GoLiveSection() {
  return (
    <section
      id="go-live"
      aria-labelledby="go-live-heading"
      className="border-y border-white/10 bg-black/25 py-20 sm:py-24"
    >
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <p className="text-sm font-bold uppercase tracking-widest text-sun/90">Step 3</p>
        <h2
          id="go-live-heading"
          className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl"
        >
          Go Live
        </h2>
        <p className="mt-4 max-w-2xl text-lg leading-relaxed text-white/65">
          Preview on a real URL, share with early users, and ship when you&apos;re ready. Ship
          handles the deploy path so you&apos;re not wrestling with hosting configs on day one —
          you stay focused on the product story.
        </p>
        <div className="mt-10 flex flex-col gap-4 sm:flex-row sm:items-center">
          <Link href="#waitlist" className="btn-primary text-center">
            Join the waitlist
          </Link>
          <Link href="/beta" className="btn-ghost text-center">
            Tell us more on the beta form →
          </Link>
        </div>
      </div>
    </section>
  );
}
