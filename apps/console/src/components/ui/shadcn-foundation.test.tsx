import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

describe("shadcn foundation", () => {
  it("renders Ship-branded Button and Card with semantic token classes", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Theme sample</CardTitle>
        </CardHeader>
        <CardContent>
          <Button>Connect</Button>
        </CardContent>
      </Card>,
    );

    const button = screen.getByRole("button", { name: "Connect" });
    expect(button.className).toMatch(/from-coral/);
    expect(button.className).toMatch(/text-ink/);

    const card = screen.getByText("Theme sample").closest("[class*='bg-card']");
    expect(card).toBeTruthy();
  });
});
