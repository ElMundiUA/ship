import type { Metadata } from "next";
import { DM_Sans, Plus_Jakarta_Sans } from "next/font/google";
import { AdoptionWizardProvider } from "@/components/adoption-wizard";
import { resolveMetadataBase } from "@/lib/site-url";
import "./globals.css";

/** Headings: readable geometric sans (replaces ultra-wide Syne for hero/body comfort). */
const heading = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-heading",
  display: "swap",
  adjustFontFallback: true,
});

const sans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-dm",
  display: "swap",
  adjustFontFallback: true,
});

export const metadata: Metadata = {
  title: "Ship — methodology your agent can run",
  description:
    "Apache-2.0 methodology kit: one site for onboarding, evidence-led use cases, and catalogs that stay aligned with how you ship.",
  metadataBase: resolveMetadataBase(),
  openGraph: {
    title: "Ship",
    description: "Sell the operating model — not another dashboard tax.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${heading.variable} ${sans.variable}`}>
      <body className="min-h-screen font-sans">
        <AdoptionWizardProvider>{children}</AdoptionWizardProvider>
      </body>
    </html>
  );
}
