import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiSdlcReadiness } from "@/lib/api/client";
import { SdlcReadinessCard } from "./sdlc-readiness-card";

function _readiness(over: Partial<ApiSdlcReadiness> = {}): ApiSdlcReadiness {
  return {
    repo_id: "r1",
    intel_id: "i1",
    project_type: "web",
    has_blueprint: true,
    ready: false,
    detail: null,
    delivery: "docker",
    environments: ["dev", "prod"],
    capabilities: [],
    gaps: ["containerization"],
    secrets: [],
    missing_required_secrets: [],
    external_checklist: [],
    ...over,
  };
}

function _mockFetch(payload: unknown, ok = true) {
  const f = vi.fn(async () => ({
    ok,
    status: ok ? 200 : 409,
    json: async () => payload,
  }));
  global.fetch = f as unknown as typeof fetch;
  return f;
}

function _render() {
  return render(
    <SdlcReadinessCard
      workspaceId="ws1"
      repoId="r1"
      repoFullName="acme/widget"
    />,
  );
}

describe("SdlcReadinessCard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("is lazy — shows a Check button and fetches nothing until clicked", () => {
    const f = vi.fn();
    global.fetch = f as unknown as typeof fetch;
    _render();
    expect(screen.getByText("Check readiness")).toBeInTheDocument();
    expect(f).not.toHaveBeenCalled();
  });

  it("renders gaps + a Generate button for a not-ready repo", async () => {
    _mockFetch(_readiness());
    _render();
    fireEvent.click(screen.getByText("Check readiness"));
    expect(await screen.findByText("containerization")).toBeInTheDocument();
    expect(
      screen.getByText("Generate bootstrap tickets"),
    ).toBeInTheDocument();
  });

  it("shows Ready + no Generate button for a ready repo", async () => {
    _mockFetch(_readiness({ ready: true, gaps: [] }));
    _render();
    fireEvent.click(screen.getByText("Check readiness"));
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(
      screen.queryByText("Generate bootstrap tickets"),
    ).not.toBeInTheDocument();
  });

  it("shows the detail when there's no blueprint", async () => {
    _mockFetch(
      _readiness({
        has_blueprint: false,
        project_type: "backend",
        gaps: [],
        detail: "No bootstrap blueprint for project_type='backend'.",
      }),
    );
    _render();
    fireEvent.click(screen.getByText("Check readiness"));
    expect(
      await screen.findByText(/No bootstrap blueprint/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Generate bootstrap tickets"),
    ).not.toBeInTheDocument();
  });

  it("generates a plan and links to the epic", async () => {
    // First call: readiness. Second call: bootstrap-plan.
    const f = vi.fn();
    f.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => _readiness(),
    });
    f.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        repo_id: "r1",
        project_url: "https://linear.app/acme/project/boot",
        project_native_id: "proj-1",
        tickets: [
          { capability: "containerization", display_id: "ELS-1", url: "u" },
        ],
      }),
    });
    global.fetch = f as unknown as typeof fetch;
    _render();
    fireEvent.click(screen.getByText("Check readiness"));
    fireEvent.click(await screen.findByText("Generate bootstrap tickets"));
    expect(await screen.findByText(/view the bootstrap epic/)).toBeInTheDocument();
    expect(await screen.findByText(/Created 1 ticket/)).toBeInTheDocument();
  });
});
