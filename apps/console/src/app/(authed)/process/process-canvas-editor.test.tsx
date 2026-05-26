import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ApiProcess } from "@/lib/api/client";
import { ProcessCanvasEditor } from "./process-canvas-editor";

function minimalProcess(states: ApiProcess["states"] = []): ApiProcess {
  return {
    id: "development",
    name: "Development",
    primary: true,
    state_count: states.length,
    task_count: 0,
    blocked_count: 0,
    health: "ok",
    description: "",
    specialists: [],
    states,
    transitions: [],
    tasks: [],
    routines: [],
    process_graph: { nodes: [], links: [] },
    adapter_diagnostics: [],
  };
}

describe("ProcessCanvasEditor add-stage affordances", () => {
  it("does not render a floating canvas-level + Add stage control", () => {
    render(
      <ProcessCanvasEditor
        process={minimalProcess()}
        onSelectState={vi.fn()}
        onAddStageInLane={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /^\+ Add stage$/i }),
    ).not.toBeInTheDocument();
  });

  it("shows a visible + Add control in each lane header", () => {
    render(
      <ProcessCanvasEditor
        process={minimalProcess()}
        onSelectState={vi.fn()}
        onAddStageInLane={vi.fn()}
      />,
    );

    const laneAdds = screen.getAllByRole("button", { name: /Add stage to/i });
    expect(laneAdds.length).toBeGreaterThan(0);
    for (const button of laneAdds) {
      expect(button).toHaveTextContent("+ Add");
      expect(button.className).not.toMatch(/opacity-0/);
    }
  });

  it("calls onAddStageInLane with the lane when a header add is clicked", () => {
    const onAddStageInLane = vi.fn();
    render(
      <ProcessCanvasEditor
        process={minimalProcess()}
        onSelectState={vi.fn()}
        onAddStageInLane={onAddStageInLane}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Add stage to Executing" }),
    );
    expect(onAddStageInLane).toHaveBeenCalledWith("executing");
  });
});
