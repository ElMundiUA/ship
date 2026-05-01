import { BookSection } from "@/components/book-section";
import { FounderQuoteSection } from "@/components/founder-quote-section";
import { HeroSection } from "@/components/hero-section";
import { HowItWorksSection } from "@/components/how-it-works-section";
import { KitSurfaceSection } from "@/components/kit-surface-section";
import { OperatorLoopSection } from "@/components/operator-loop-section";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { TeamSection } from "@/components/team-section";

export default function Home() {
  return (
    <>
      <SiteHeader />
      <main>
        <HeroSection />
        <FounderQuoteSection />
        <HowItWorksSection />
        <OperatorLoopSection />
        <KitSurfaceSection />
        <BookSection />
        <TeamSection />
      </main>
      <SiteFooter />
    </>
  );
}
