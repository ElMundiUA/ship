import { SettingsShell } from "../_shell/settings-shell";

export const dynamic = "force-dynamic";

export const metadata = { title: "Registries — Workspace settings" };

export default function RegistriesSettingsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <SettingsShell activeTab="registries" searchParams={searchParams} />;
}
