import { DogfoodSection } from "@/components/dogfood-section";
import { FounderQuoteSection } from "@/components/founder-quote-section";
import { HeroSection } from "@/components/hero-section";
import { ModelSection } from "@/components/model-section";
import { ProblemSection } from "@/components/problem-section";
import { RoadmapTeaserSection } from "@/components/roadmap-teaser-section";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { TeamSection } from "@/components/team-section";

/**
 * Homepage narrative arc — eight beats, each one adding to the argument
 * rather than restating it.
 *
 *   1. Hero — what Ship is, with a real process mock.
 *   2. ProblemSection — the pain (invisible processes drift) + the
 *      resolution (legible processes can be improved). Honest before /
 *      after with stylised infographics.
 *   3. ModelSection — the four-layer stack
 *      (workspace → process → routine → specialist → executor) with a
 *      column infographic. Cross-links to /process for the deep walk.
 *   4. FounderQuoteSection — the personal angle, "Vibes don't ship".
 *   5. DogfoodSection — Ship building Ship. 417 commits in 25 days,
 *      8.9% authored by routines, 11 ELS tickets, 18 blog posts. Numbers
 *      come from the public git history and blog index.
 *   6. RoadmapTeaserSection — north-star + Now / Next / Later teaser.
 *   7. TeamSection — credibility for investors and serious buyers.
 *   8. (CTA lives inside the closing CTAs of TeamSection /
 *      DogfoodSection / RoadmapTeaser; no separate marketing closer
 *      to keep the page from running long.)
 */
export default function Home() {
  return (
    <>
      <SiteHeader />
      <main>
        <HeroSection />
        <ProblemSection />
        <ModelSection />
        <FounderQuoteSection />
        <DogfoodSection />
        <RoadmapTeaserSection />
        <TeamSection />
      </main>
      <SiteFooter />
    </>
  );
}
