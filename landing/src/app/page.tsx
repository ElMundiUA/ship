import { BackendStrip } from "@/components/backend-strip";
import { BookSection } from "@/components/book-section";
import { CommandBuilderSection } from "@/components/command-builder-section";
import { ExamplesSection } from "@/components/examples-section";
import { HeroSection } from "@/components/hero-section";
import { HowItWorksSection } from "@/components/how-it-works-section";
import { KitSurfaceSection } from "@/components/kit-surface-section";
import { PatternsSection } from "@/components/patterns-section";
import { PillarsSection } from "@/components/pillars-section";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export default function Home() {
  return (
    <>
      <SiteHeader />
      <main>
        <HeroSection />
        <HowItWorksSection />
        <CommandBuilderSection />
        <PillarsSection />
        <KitSurfaceSection />
        <BookSection />
        <PatternsSection />
        <ExamplesSection />
        <BackendStrip />
      </main>
      <SiteFooter />
    </>
  );
}
