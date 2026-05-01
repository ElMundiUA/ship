import { resolveMetadataBase } from "@/lib/site-url";
import { repoUrl } from "@/lib/config";

/**
 * JSON-LD structured data injected into the root ``<head>``.
 *
 * Three schemas are emitted:
 *
 * - ``Organization`` — the company entity behind Ship. Powers Google's
 *   knowledge-panel snippet and "About" cards on social.
 * - ``WebSite`` — the public site itself. Lets search engines associate
 *   the URL with the brand and (when implemented) surface a sitelinks
 *   search box.
 * - ``SoftwareApplication`` — the product. Helps Google show structured
 *   results on product / "AI tools" queries with name, description, and
 *   category.
 *
 * Kept as a single component so the schemas live next to each other and
 * stay in sync with the ``Metadata`` block in ``layout.tsx``. Output is a
 * single ``<script type="application/ld+json">`` per schema; Next.js
 * inlines them into the document head.
 */
export function StructuredData() {
  const siteUrl = resolveMetadataBase().toString().replace(/\/$/, "");
  const logoUrl = `${siteUrl}/icon`;

  const organization = {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: "Ship",
    legalName: "ElMundi UA",
    url: siteUrl,
    logo: logoUrl,
    description:
      "A workspace for AI-assisted product delivery: humans own intent, machines act inside fences, every action leaves a trail.",
    foundingDate: "2025",
    sameAs: [repoUrl, "https://elmundi.com"],
  };

  const website = {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: "Ship",
    url: siteUrl,
    description:
      "Ship connects repos, trackers, policies, knowledge, processes, and evidence so solo founders and product owners can steer AI-assisted delivery.",
    publisher: { "@type": "Organization", name: "Ship" },
    inLanguage: "en",
  };

  const softwareApplication = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "Ship",
    operatingSystem: "Web (Cloud), macOS, Windows, Linux",
    applicationCategory: "DeveloperApplication",
    description:
      "Ship is a workspace for AI-assisted product delivery. It runs the Development SDLC as a process on versioned specialists, captures decisions in a structured Inbox, and keeps an audit trail of every routine that touches a repository.",
    url: siteUrl,
    offers: {
      "@type": "Offer",
      availability: "https://schema.org/PreOrder",
      price: "0",
      priceCurrency: "USD",
      description: "Closed beta — by invite",
    },
    softwareVersion: "0.14",
    license: "https://www.apache.org/licenses/LICENSE-2.0",
    publisher: { "@type": "Organization", name: "Ship" },
  };

  return (
    <>
      <script
        type="application/ld+json"
        // Next.js requires dangerouslySetInnerHTML for raw JSON-LD; the
        // payload is fully under our control (no user input) so this is
        // safe.
        dangerouslySetInnerHTML={{ __html: JSON.stringify(organization) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(website) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(softwareApplication) }}
      />
    </>
  );
}
