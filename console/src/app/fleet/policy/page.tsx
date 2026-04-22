import { FleetStub } from "@/components/fleet-stub";

export default function FleetPolicyStubPage() {
  return (
    <FleetStub
      title="Policy"
      shipsIn="PR-5"
      summary="Cross-repo rules and mirror patterns enforced at the workspace level — the place where you say 'every repo runs the security-scan pattern nightly' instead of editing N .ship/config.yml files by hand."
      bullets={[
        "Mirror a catalog pattern onto every activated repo (opt-in / opt-out per repo).",
        "Required-check rules (e.g. every request of kind `qa` must pass before merge).",
        "Drift view: repos where .ship/config.yml diverges from the workspace policy.",
        "Autofix via Navigator — one PR per repo to bring the config back in line.",
      ]}
    />
  );
}
