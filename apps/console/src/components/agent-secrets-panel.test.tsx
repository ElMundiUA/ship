import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiAgentSecretStatus } from "@/lib/api/client";
import type { ApiActivatedRepo } from "@/lib/api/types";
import { AgentSecretsPanel } from "./agent-secrets-panel";

const REPO = { id: "repo-1", full_name: "org/repo" } as ApiActivatedRepo;

function secret(extra: Partial<ApiAgentSecretStatus>): ApiAgentSecretStatus {
  return {
    slug: "claude-md",
    label: "Claude Code",
    secret_name: "ANTHROPIC_API_KEY",
    vendor_url: null,
    description: null,
    required: true,
    present: false,
    ...extra,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("AgentSecretsPanel", () => {
  it("shows a paste input for a PRESENT key (rotation) and saves drafts", async () => {
    const agents = [
      secret({ slug: "claude-md", label: "Claude Code", secret_name: "ANTHROPIC_API_KEY", present: true }),
      secret({ slug: "cursor-cloud", label: "Cursor Cloud", secret_name: "CURSOR_API_KEY", present: false }),
    ];
    const calls: { url: string; init?: { method?: string; body?: string } }[] = [];
    const fetchMock = vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
      calls.push({ url, init });
      if (init?.method === "POST") {
        return { ok: true, json: async () => ({ result: { pushed: ["claude-md"], failed: [] } }) };
      }
      return { ok: true, json: async () => ({ check: { repo_id: "repo-1", agents } }) };
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<AgentSecretsPanel workspaceId="ws-1" repos={[REPO]} />);

    // Status is fetched on mount for the (only) repo.
    await waitFor(() => expect(screen.getByText("Claude Code")).toBeInTheDocument());
    const getCall = calls.find((c) => c.init?.method !== "POST");
    expect(getCall?.url).toContain("workspace_id=ws-1");
    expect(getCall?.url).toContain("repo_id=repo-1");

    // THE FIX: a present key still renders an input so it can be rotated.
    const claudeInput = screen.getByPlaceholderText(
      /ANTHROPIC_API_KEY — paste a new key to replace/,
    );
    expect(claudeInput).toBeInTheDocument();

    fireEvent.change(claudeInput, { target: { value: "sk-ant-new" } });
    fireEvent.click(screen.getByRole("button", { name: /Save keys/i }));

    await waitFor(() =>
      expect(calls.some((c) => c.init?.method === "POST")).toBe(true),
    );
    const post = calls.find((c) => c.init?.method === "POST");
    expect(JSON.parse(post!.init!.body!)).toMatchObject({
      workspace_id: "ws-1",
      repo_id: "repo-1",
      secrets: [{ slug: "claude-md", plaintext: "sk-ant-new" }],
    });
  });

  it("prompts to activate a repo when there are none", () => {
    render(<AgentSecretsPanel workspaceId="ws-1" repos={[]} />);
    expect(screen.getByText(/Activate a repository first/i)).toBeInTheDocument();
  });
});
