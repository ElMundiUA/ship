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

  it("uses the live catalogue dropdown for Cursor and patches the picked model", async () => {
    // Cursor now has a backend-served catalogue (platform key → cursor API),
    // so it's a select fed by /api/deploy/planner-models?provider=cursor —
    // not free-text. Picking an option patches specialist_model.
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ models: ["auto", "composer-2.5", "claude-opus-4-8"] }),
    }));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

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

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, { body: string }];
    expect(JSON.parse(init.body)).toMatchObject({ provider: "cursor" });

    const modelControl = await screen.findByLabelText(/Model/i);
    expect(modelControl).not.toHaveAttribute("list");
    fireEvent.change(modelControl, { target: { value: "composer-2.5" } });
    expect(onStateChange).toHaveBeenCalledWith(
      expect.objectContaining({ specialist_model: "composer-2.5" }),
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

  it("spells out the workspace provider on the 'Workspace default' backend option", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ models: [] }) })) as unknown as typeof fetch,
    );
    render(
      <StateEditor
        workspaceId="ws-1"
        agentProvider="cursor"
        repoId="repo-1"
        state={stateWith({ specialist_agent_profile: "main" } as Partial<ApiProcessState>)}
        states={[stateWith()]}
        schedule={null}
        transitions={[]}
        specialistOptions={[{ id: "intake", name: "Intake", role: "Intake" }]}
        config={null}
        onStateChange={vi.fn()}
        onDeleteState={vi.fn()}
      />,
    );
    const backend = screen.getByLabelText(/Execution backend/i);
    expect(backend).toHaveTextContent("Workspace default · Cursor");
  });

  it("resets the model to provider default when the backend switches provider", async () => {
    // Stage runs Cursor with composer-2.5 picked; switching the backend to
    // Codex CLI (openai) must clear the now-invalid model, not keep composer.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ models: [] }) })) as unknown as typeof fetch,
    );
    const onStateChange = vi.fn();
    render(
      <StateEditor
        workspaceId="ws-1"
        agentProvider="cursor"
        repoId="repo-1"
        state={stateWith({
          specialist_agent_profile: "cursor_agent",
          specialist_model: "composer-2.5",
        } as Partial<ApiProcessState>)}
        states={[stateWith()]}
        schedule={null}
        transitions={[]}
        specialistOptions={[{ id: "intake", name: "Intake", role: "Intake" }]}
        config={null}
        onStateChange={onStateChange}
        onDeleteState={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Execution backend/i), {
      target: { value: "codex_cli" },
    });
    expect(onStateChange).toHaveBeenCalledWith(
      expect.objectContaining({
        specialist_agent_profile: "codex_cli",
        specialist_model: null,
      }),
    );
  });

  it("keeps the model when the backend stays on the same provider", async () => {
    // main → local_cli both resolve to the workspace provider — no reset.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ models: [] }) })) as unknown as typeof fetch,
    );
    const onStateChange = vi.fn();
    render(
      <StateEditor
        workspaceId="ws-1"
        agentProvider="claude"
        repoId="repo-1"
        state={stateWith({
          specialist_agent_profile: "main",
          specialist_model: "claude-opus-4-8",
        } as Partial<ApiProcessState>)}
        states={[stateWith()]}
        schedule={null}
        transitions={[]}
        specialistOptions={[{ id: "intake", name: "Intake", role: "Intake" }]}
        config={null}
        onStateChange={onStateChange}
        onDeleteState={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Execution backend/i), {
      target: { value: "claude_code" },
    });
    // claude_code and main both → anthropic here, so the model is preserved.
    const call = onStateChange.mock.calls.at(-1)?.[0];
    expect(call).toMatchObject({ specialist_agent_profile: "claude_code" });
    expect(call).not.toHaveProperty("specialist_model", null);
  });

  it("Claude Code backend shows anthropic models even on a Cursor workspace", async () => {
    // Workspace default is Cursor, but this stage pins Claude Code — the
    // picker must query the anthropic catalogue, matching what the runtime
    // now actually runs for the stage.
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ models: ["claude-opus-4-8"] }),
    }));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(
      <StateEditor
        workspaceId="ws-1"
        agentProvider="cursor"
        repoId="repo-1"
        state={stateWith({ specialist_agent_profile: "claude_code" } as Partial<ApiProcessState>)}
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
    expect(JSON.parse(init.body)).toMatchObject({ provider: "anthropic" });
    const modelControl = await screen.findByLabelText(/Model/i);
    expect(modelControl).not.toHaveAttribute("list");
  });
});
