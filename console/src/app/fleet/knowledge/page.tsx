import { FleetStub } from "@/components/fleet-stub";

export default function FleetKnowledgeStubPage() {
  return (
    <FleetStub
      title="Knowledge graph"
      shipsIn="PR-7"
      summary="Cross-repo knowledge: propagate runbook updates, architectural decisions, and onboarding docs between repos that share patterns. Per-repo buckets live under repo mode; this is where 'tell every payments repo about the new PCI runbook' happens."
      bullets={[
        "Visualize edges between knowledge buckets across repos (shared tags, shared patterns, shared owners).",
        "Propagation: 'apply this chunk to every repo tagged payments' with a diff view before commit.",
        "Search across the whole workspace's knowledge (repo-scoped search stays under the repo nav).",
        "Stale detector — bucket hasn't been touched in N months while the underlying pattern moved.",
      ]}
    />
  );
}
