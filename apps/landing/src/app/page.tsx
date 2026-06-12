import type { Metadata } from "next";
import { BuildSection } from "@/components/build-section";
import { DescribeSection } from "@/components/describe-section";
import { GoLiveSection } from "@/components/go-live-section";
import { HeroSection } from "@/components/hero-section";
import { HomepageWaitlistSection } from "@/components/homepage-waitlist-section";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Describe it. Ship it. Go live.",
  description:
    "Ship turns your idea into a live app for non-technical founders — describe what you want, Ship builds it, you share a real preview.",
  openGraph: {
    title: "Ship — if you can describe it, you can ship it",
    description:
      "Describe your idea in plain English. Ship scaffolds, builds, and ships a preview you can share — built for founders, not operators.",
  },
  twitter: {
    title: "Ship — if you can describe it, you can ship it",
    description:
      "Describe your idea in plain English. Ship scaffolds, builds, and ships a preview you can share.",
  },
};

/**
 * Founder-focused homepage — describe → build → go live, with on-page waitlist CTA.
 */
export default function Home() {
  return (
    <>
      <SiteHeader />
      <main>
        <HeroSection />
        <DescribeSection />
        <BuildSection />
        <GoLiveSection />
        <HomepageWaitlistSection />
      </main>
      <SiteFooter />
    </>
  );
}
