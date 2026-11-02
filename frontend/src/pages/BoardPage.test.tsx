import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Link, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MODEL, makeBoard, makeDeparture } from "../test/fixtures";
import { BoardPage } from "./BoardPage";

const BOARDS: Record<string, unknown> = {
  "8000105": makeBoard({
    station: { eva: "8000105", name: "Frankfurt (Main) Hbf" },
    departures: [makeDeparture({ event_id: "f1" })],
  }),
  "8000261": makeBoard({
    station: { eva: "8000261", name: "München Hbf" },
    departures: [
      makeDeparture({
        event_id: "m1",
        train: { type: "ICE", number: "599", line: null, destination: "Berlin Hbf" },
      }),
    ],
  }),
};

function respond(url: string): Response {
  const board = /stations\/(\d+)\/departures/.exec(url);
  if (board?.[1]) return new Response(JSON.stringify(BOARDS[board[1]]), { status: 200 });
  if (url.includes("/model")) return new Response(JSON.stringify(MODEL), { status: 200 });
  return new Response("[]", { status: 200 });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BoardPage", () => {
  it("closes the detail when the station changes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => Promise.resolve(respond(String(input)))),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/station/8000105"]}>
          <Routes>
            <Route
              path="/station/:eva"
              element={
                <>
                  <BoardPage />
                  <Link to="/station/8000261">Go to München</Link>
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /Göttingen/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Go to München" }));
    expect(await screen.findByRole("heading", { name: "München Hbf" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

function train(id: string, destination: string) {
  return makeDeparture({
    event_id: id,
    train: { type: "ICE", number: id, line: null, destination },
  });
}

const FILTER_BOARDS: Record<string, unknown> = {
  "8000284": makeBoard({
    station: { eva: "8000284", name: "Nürnberg Hbf" },
    departures: [train("1", "München Hbf"), train("2", "Erlangen"), train("3", "München Hbf")],
  }),
  // The 1 h window of the same station has no train to Erlangen.
  "8000284-1": makeBoard({
    station: { eva: "8000284", name: "Nürnberg Hbf" },
    departures: [train("1", "München Hbf")],
  }),
  "8000261": makeBoard({
    station: { eva: "8000261", name: "München Hbf" },
    departures: [train("9", "Erlangen"), train("8", "Berlin Hbf")],
  }),
};

function renderFilterBoard() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const match = /stations\/(\d+)\/departures\?hours=(\d)/.exec(url);
      if (match) {
        const body = FILTER_BOARDS[`${match[1]}-${match[2]}`] ?? FILTER_BOARDS[match[1] ?? ""];
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }
      return Promise.resolve(respond(url));
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/station/8000284"]}>
        <Routes>
          <Route
            path="/station/:eva"
            element={
              <>
                <BoardPage />
                <Link to="/station/8000261">Go to München</Link>
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const rows = () => screen.getByRole("list", { name: /Departures from/ }).querySelectorAll("li");

describe("Going to filter", () => {
  it("offers the board's final stops and shows only trains to the chosen one", async () => {
    renderFilterBoard();
    const filter = await screen.findByRole("combobox", { name: /Going to/ });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "All destinations",
      "Erlangen",
      "München Hbf",
    ]);
    expect(rows()).toHaveLength(3);
    await userEvent.selectOptions(filter, "München Hbf");
    expect(rows()).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Erlangen/ })).not.toBeInTheDocument();
  });

  it("says the filter matches the train's final stop", async () => {
    renderFilterBoard();
    expect(await screen.findByRole("combobox", { name: /final stop/i })).toBeInTheDocument();
  });

  it("resets when the station changes", async () => {
    renderFilterBoard();
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: /Going to/ }),
      "Erlangen",
    );
    await userEvent.click(screen.getByRole("link", { name: "Go to München" }));
    await screen.findByRole("heading", { name: "München Hbf" });
    expect(screen.getByRole("combobox", { name: /Going to/ })).toHaveValue("");
    expect(rows()).toHaveLength(2);
  });

  it("keeps the choice across time windows and offers all trains when none match", async () => {
    renderFilterBoard();
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: /Going to/ }),
      "Erlangen",
    );
    await userEvent.click(screen.getByRole("button", { name: "1 h" }));
    // The station name sits in its own lang="de" span, so match the whole paragraph.
    expect(
      await screen.findByText(
        (_, element) =>
          element?.tagName === "P" &&
          element.textContent === "No trains to Erlangen in the next hour",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /Going to/ })).toHaveValue("Erlangen");
    await userEvent.click(screen.getByRole("button", { name: "Show all trains" }));
    expect(rows()).toHaveLength(1);
    expect(screen.getByRole("combobox", { name: /Going to/ })).toHaveValue("");
  });
});
