import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RiskBadge } from "./RiskBadge";

describe("RiskBadge", () => {
  it.each([
    ["low", 0.12, "Low · 12%", "Delay risk low, 12 percent"],
    ["medium", 0.34, "Medium · 34%", "Delay risk medium, 34 percent"],
    ["high", 0.61, "High · 61%", "Delay risk high, 61 percent"],
  ] as const)("%s shows icon, word and number", (level, p, text, name) => {
    render(<RiskBadge prediction={{ p_late: p, risk_level: level, top_factors: [] }} />);
    const badge = screen.getByRole("img", { name });
    expect(badge).toHaveTextContent(text);
    expect(badge.querySelector("svg")).not.toBeNull();
  });
  it("cancelled wins over a forecast", () => {
    render(
      <RiskBadge prediction={{ p_late: 0.5, risk_level: "high", top_factors: [] }} cancelled />,
    );
    expect(screen.getByRole("img", { name: "Cancelled" })).toHaveTextContent("Cancelled");
  });
  it("no forecast", () => {
    render(<RiskBadge prediction={null} />);
    expect(screen.getByRole("img", { name: "No forecast available" })).toHaveTextContent(
      "No forecast",
    );
  });
});
