import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { DocsSidebar } from "@/components/docs-sidebar";

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-ink pt-16">
      <SiteHeader />
      <DocsSidebar>{children}</DocsSidebar>
      <SiteFooter />
    </div>
  );
}
