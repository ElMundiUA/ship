import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiProcessState } from "@/lib/api/client";
import { StateEditor } from "./state-editor";

function stateWith(extra: Partial<ApiProcessState> = {}): ApiProcessState {
  return {
    id: "planning",
    name: "Planning",
    specialist_id: "intake",
    specialist_name: "Intake",
    instructions: "",
    state: "planning",
    layout: null,
    triggers: [{ type: "manual", interval: null, event: null }],
    exit_conditions: [],
    block_conditions: [],
    runtime: {
      task_count: 0,
      blocked_count: 0,
      last_execution_time: null,
      health: "ok",
    },
    ...extra,
  } as ApiProcessState;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StateEditor model picker", () => {
  it("loads provider models and patches specialist_model on change", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ models: ["claude-sonnet-4-6", "claude-opus-4-8"] }),
    }));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const onStateChange = vi.fn();
    render(
      <StateEditor
        processId="development"
        workspaceId="ws-1"
        agentProvider="claude"
        repoId="repo-1"
        state={stateWith()}
        states={[stateWith()]}
        schedule={null}
        transitions={[]}
        specialistOptions={[{ id: "intake", name: "Intake", role: "Intake" }]}
        config={null}
        onStateChange={onStateChange}
        onDeleteState={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, { body: string }];
    expect(JSON.parse(init.body)).toMatchObject({ provider: "anthropic", repoId: "repo-1" });

    const modelSelect = await screen.findByLabelText(/Model/i);
    await waitFor(() =>
      expect(
        screen.getByRole("option", { name: "claude-opus-4-8" }),
      ).toBeInTheDocument(),
    );

    fireEvent.change(modelSelect, { target: { value: "claude-opus-4-8" } });
    expect(onStateChange).toHaveBeenCalledWith(
      expect.objectContaining({ specialist_model: "claude-opus-4-8" }),
    );
  });

  it("selecting the blank option clears the model (provider default)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ models: ["gpt-5-codex"] }),
      })) as unknown as typeof fetch,
    );

    const onStateChange = vi.fn();
    render(
      <StateEditor
        workspaceId="ws-1"
        agentProvider="codex"
        repoId="repo-1"
        state={stateWith({ specialist_model: "gpt-5-codex" } as Partial<ApiProcessState>)}
        states={[stateWith()]}
        schedule={null}
        transitions={[]}
        specialistOptions={[{ id: "intake", name: "Intake", role: "Intake" }]}
        config={null}
        onStateChange={onStateChange}
        onDeleteState={vi.fn()}
      />,
    );

    const modelSelect = await screen.findByLabelText(/Model/i);
    fireEvent.change(modelSelect, { target: { value: "" } });
    expect(onStateChange).toHaveBeenCalledWith(
      expect.objectContaining({ specialist_model: null }),
    );
  });

  it("uses a free-text field for Cursor (no catalogue) and patches the typed model", () => {
    // Cursor has no models.dev catalogue, so no fetch — it's a free-text input
    // (with suggestions) where Cursor-native models like Composer can be typed.
    const onStateChange = vi.fn();
    render(
      <StateEditor
        workspaceId="ws-1"
        agentProvider="cursor"
        repoId="repo-1"
        state={stateWith()}
        states={[stateWith()]}
        schedule={null}
        transitions={[]}
        specialistOptions={[{ id: "intake", name: "Intake", role: "Intake" }]}
        config={null}
        onStateChange={onStateChange}
        onDeleteState={vi.fn()}
      />,
    );

    const modelInput = screen.getByLabelText(/Model/i);
    expect(modelInput).toHaveAttribute("list", "cursor-model-suggestions");
    fireEvent.change(modelInput, { target: { value: "composer-1" } });
    expect(onStateChange).toHaveBeenCalledWith(
      expect.objectContaining({ specialist_model: "composer-1" }),
    );
  });

  it("model picker follows the per-stage execution backend, not the workspace default", async () => {
    // Workspace default is Cursor, but this stage runs Codex CLI — the picker
    // must show OpenAI models (catalogue select), not Cursor free-text/composer.
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ models: ["gpt-5-codex"] }),
    }));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(
      <StateEditor
        workspaceId="ws-1"
        agentProvider="cursor"
        repoId="repo-1"
        state={stateWith({ specialist_agent_profile: "codex_cli" } as Partial<ApiProcessState>)}
        states={[stateWith()]}
        schedule={null}
        transitions={[]}
        specialistOptions={[{ id: "intake", name: "Intake", role: "Intake" }]}
        config={null}
        onStateChange={vi.fn()}
        onDeleteState={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, { body: string }];
    expect(JSON.parse(init.body)).toMatchObject({ provider: "openai" });
    // Catalogue select, not the Cursor free-text input (which has a datalist).
    const modelControl = await screen.findByLabelText(/Model/i);
    expect(modelControl).not.toHaveAttribute("list");
  });
});
