import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Departure } from "../api/types";
import { MODEL, makeDeparture } from "../test/fixtures";
import { DepartureDetail } from "./DepartureDetail";

function open(overrides: Partial<Departure> = {}, onClose = vi.fn()) {
  render(
    <DepartureDetail
      departure={makeDeparture(overrides)}
      thresholds={MODEL.risk_thresholds}
      modelVersion="1"
      trainedAt={MODEL.trained_at}
      dataAsOf="2026-10-05T15:30:00+02:00"
      onClose={onClose}
    />,
  );
}

describe("DepartureDetail", () => {
  it("is a labelled modal dialog with the close button focused", () => {
    open();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName(/17:42/);
    expect(screen.getByRole("button", { name: "Close details" })).toHaveFocus();
  });
  it("shows the probability and the reasons", () => {
    open();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "34");
    expect(screen.getByText("34% chance of leaving 6+ minutes late")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("Late stop in a long journey")).toBeInTheDocument();
    expect(screen.getByText(/Model v1/)).toHaveTextContent(
      "Model v1 · trained 4 Oct 2026 · data as of 15:30",
    );
  });
  it("closes on Escape", async () => {
    const onClose = vi.fn();
    open({}, onClose);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
  it("explains a cancelled departure", () => {
    open({ cancelled: true, prediction: null });
    expect(screen.getByText("This departure is cancelled.")).toBeInTheDocument();
  });
  it("keeps Tab inside the dialog", async () => {
    open();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Close details" })).toHaveFocus();
  });
});
