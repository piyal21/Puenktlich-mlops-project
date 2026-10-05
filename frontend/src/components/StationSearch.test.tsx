import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Station } from "../api/types";
import { StationSearch } from "./StationSearch";

const OPTIONS: Station[] = [
  { eva: "8010101", name: "Erfurt Hbf", state: "TH" },
  { eva: "8000098", name: "Essen Hbf", state: "NW" },
];

function Harness({ onSelect }: { onSelect: (station: Station) => void }) {
  const [query, setQuery] = useState("");
  return (
    <StationSearch query={query} onQueryChange={setQuery} options={OPTIONS} onSelect={onSelect} />
  );
}

describe("StationSearch", () => {
  it("is a labelled combobox", () => {
    render(<Harness onSelect={vi.fn()} />);
    const box = screen.getByRole("combobox", { name: "Station" });
    expect(box).toHaveAttribute("placeholder", "Search a station, e.g. Erfurt Hbf");
    expect(box).toHaveAttribute("aria-expanded", "false");
  });
  it("supports arrow keys and Enter", async () => {
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    const box = screen.getByRole("combobox");
    await userEvent.type(box, "e");
    expect(box).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    const essen = screen.getByRole("option", { name: "Essen Hbf" });
    expect(essen).toHaveAttribute("aria-selected", "true");
    expect(box.getAttribute("aria-activedescendant")).toBe(essen.id);
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith(OPTIONS[1]);
  });
  it("closes on Escape", async () => {
    render(<Harness onSelect={vi.fn()} />);
    await userEvent.type(screen.getByRole("combobox"), "e");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
  it("selects with a click", async () => {
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    await userEvent.type(screen.getByRole("combobox"), "er");
    await userEvent.click(screen.getByRole("option", { name: "Erfurt Hbf" }));
    expect(onSelect).toHaveBeenCalledWith(OPTIONS[0]);
  });
});
