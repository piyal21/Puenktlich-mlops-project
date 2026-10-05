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
