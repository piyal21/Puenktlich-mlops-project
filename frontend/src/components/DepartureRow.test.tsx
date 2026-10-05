import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Departure } from "../api/types";
import { makeDeparture } from "../test/fixtures";
import { DepartureRow } from "./DepartureRow";

function renderRow(departure: Departure = makeDeparture(), onOpen = vi.fn()) {
  render(
    <ul>
      <DepartureRow departure={departure} onOpen={onOpen} />
    </ul>,
  );
}

describe("DepartureRow", () => {
  it("shows time, train, destination, platform and risk", () => {
    renderRow();
    expect(screen.getByText("17:42")).toBeInTheDocument();
    expect(screen.getByText("Göttingen")).toHaveAttribute("lang", "de");
    expect(screen.getByText("Pl. 3")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Delay risk medium/ })).toBeInTheDocument();
  });
  it("shows the live delay", () => {
    renderRow(makeDeparture({ live_departure: "2026-10-05T17:50:00+02:00", live_delay_min: 8 }));
    expect(screen.getByText("17:50 +8")).toHaveClass("text-risk-high");
  });
  it("cancelled rows are struck through and dimmed", () => {
    renderRow(
      makeDeparture({
        cancelled: true,
        prediction: null,
        live_departure: null,
        live_delay_min: null,
      }),
    );
    expect(screen.getByText("17:42")).toHaveClass("line-through");
    expect(screen.getByRole("button")).toHaveClass("opacity-70");
    expect(screen.getByRole("img", { name: "Cancelled" })).toBeInTheDocument();
  });
  it("opens the detail on click", async () => {
    const onOpen = vi.fn();
    const departure = makeDeparture();
    renderRow(departure, onOpen);
    await userEvent.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalledWith(departure);
  });
});
