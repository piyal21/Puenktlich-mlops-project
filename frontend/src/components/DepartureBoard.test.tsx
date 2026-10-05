import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DeparturesResponse } from "../api/types";
import { makeBoard, makeDeparture } from "../test/fixtures";
import { DepartureBoard } from "./DepartureBoard";

const NOW = new Date("2026-10-05T13:57:00Z"); // 27 min after data_as_of 15:30 CEST

interface Options {
  data?: DeparturesResponse;
  isPending?: boolean;
  error?: Error | null;
}

function show({ data, isPending = false, error = null }: Options) {
  const handlers = { onRetry: vi.fn(), onShowMore: vi.fn(), onOpen: vi.fn() };
  render(
    <DepartureBoard
      data={data}
      isPending={isPending}
      error={error}
      hours={3}
      now={NOW}
      {...handlers}
    />,
  );
  return handlers;
}

describe("DepartureBoard states", () => {
  it("loading shows skeleton rows", () => {
    show({ isPending: true });
    expect(screen.getByRole("status", { name: "Loading departures" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
  });
  it("rows", () => {
    show({ data: makeBoard() });
    expect(screen.getByRole("list", { name: "Departures from Erfurt Hbf" }).children).toHaveLength(
      2,
    );
  });
  it("empty offers six hours", async () => {
    const { onShowMore } = show({ data: makeBoard({ departures: [] }) });
    expect(screen.getByText("No departures in the next 3 hours")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show 6 hours" }));
    expect(onShowMore).toHaveBeenCalled();
  });
  it("error without data offers a retry", async () => {
    const { onRetry } = show({ error: new Error("x") });
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load departures.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });
  it("error with data keeps the last good board", () => {
    show({ error: new Error("x"), data: makeBoard() });
    expect(screen.getByRole("alert")).toHaveTextContent("Showing the last data we have.");
    expect(screen.getAllByRole("img", { name: /Delay risk/ })).toHaveLength(2);
  });
  it("stale data says how old it is", () => {
    show({ data: makeBoard({ stale: true }) });
    expect(screen.getByText("Live data is 27 min old.")).toBeInTheDocument();
  });
  it("no model shows no-forecast badges and a banner", () => {
    const departures = [makeDeparture({ prediction: null })];
    show({ data: makeBoard({ model_version: null, departures }) });
    expect(screen.getByText("Forecasts are temporarily unavailable.")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "No forecast available" })).toBeInTheDocument();
  });
  it("sample data is labelled", () => {
    show({ data: makeBoard({ data_source: "sample", replayed_from: "2026-08-24" }) });
    expect(screen.getByText(/Sample data: real departures from 24 Aug 2026/)).toBeInTheDocument();
  });
});
