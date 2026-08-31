import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { axe } from "jest-axe";
import { Button } from "@/components/ui/button";

describe("a11y", () => {
  it("button has no serious axe violations", async () => {
    const { container } = render(<Button aria-label="Save">Save</Button>);
    const results = await axe(container);
    // filter only serious/critical
    const serious = results.violations.filter((v: { impact?: string | null }) =>
      ["serious", "critical"].includes(v.impact ?? "")
    );
    expect(serious).toEqual([]);
  });

  it("claims search input has accessible name", async () => {
    const { container } = render(
      <div>
        <label htmlFor="claim-search">Search claims</label>
        <input id="claim-search" aria-label="Search claims" />
      </div>
    );
    const results = await axe(container);
    const serious = results.violations.filter((v: { impact?: string | null }) =>
      ["serious", "critical"].includes(v.impact ?? "")
    );
    expect(serious).toEqual([]);
  });
});


