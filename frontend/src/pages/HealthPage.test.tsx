import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Monitoring } from "../api/types";
import { MODEL, MONITORING, RETRAIN, historyEntry } from "../test/fixtures";
import { HealthPage } from "./HealthPage";

const EMPTY: Monitoring = { latest: null, history: [], retrains: [] };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function renderWith(model: Response, monitoring: Response = json(EMPTY)) {
  const fetchMock = vi.fn((url: string) =>
    Promise.resolve(url.includes("/monitoring") ? monitoring.clone() : model.clone()),
  );
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <HealthPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-19T10:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("HealthPage", () => {
  it("shows the champion's real test metrics", async () => {
    renderWith(json(MODEL));
    expect(await screen.findByText("v1")).toBeInTheDocument();
    expect(screen.getByText("0.141")).toBeInTheDocument();
    expect(screen.getByText("Baseline 0.152 · lower is better")).toBeInTheDocument();
    expect(screen.getByText("0.808")).toBeInTheDocument();
  });
  it("explains a missing model", async () => {
    const problem = {
      type: "/errors/model-unavailable",
      title: "Forecasts are temporarily unavailable",
      status: 503,
      detail: "",
      instance: "/api/v1/model",
      request_id: "r",
    };
    renderWith(json(problem, 503));
    expect(await screen.findByText("No forecast model is live right now.")).toBeInTheDocument();
  });
  it("renders the empty state without monitoring", async () => {
    renderWith(json(MODEL));
    expect(
      await screen.findByText("Daily monitoring starts after the first nightly check."),
    ).toBeInTheDocument();
  });
  it("shows the status, last checked day and daily charts", async () => {
    renderWith(json(MODEL), json(MONITORING));
    const status = await screen.findByRole("status", { name: /monitoring status/i });
    expect(within(status).getByText("OK")).toBeInTheDocument();
    expect(within(status).getByText(/18 Nov 2026/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Brier score per day/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Late rate per day/ })).toBeInTheDocument();
  });
  it("shows Monitor late when the newest day is old", async () => {
    vi.setSystemTime(new Date("2026-11-22T10:00:00Z"));
    renderWith(json(MODEL), json(MONITORING));
    const status = await screen.findByRole("status", { name: /monitoring status/i });
    expect(within(status).getByText("Monitor late")).toBeInTheDocument();
  });
  it("shows 3 bad days in a row after three bad days", async () => {
    const bad = {
      drift_bad: false,
      performance_bad: true,
      drift_share_max: 0.3,
      brier_ratio_max: 1.1,
    };
    const history = ["2026-11-16", "2026-11-17", "2026-11-18"].map((d) =>
      historyEntry(d, { verdict: bad, brier_ratio: 1.2 }),
    );
    renderWith(json(MODEL), json({ ...MONITORING, history }));
    const status = await screen.findByRole("status", { name: /monitoring status/i });
    expect(within(status).getByText("3 bad days in a row")).toBeInTheDocument();
    expect(within(status).getByText(/decision appears under Retraining/)).toBeInTheDocument();
  });
  it("marks calendar columns as not counted in the drift table", async () => {
    renderWith(json(MODEL), json(MONITORING));
    const table = await screen.findByRole("table", { name: /drift per model input/i });
    const month = within(table).getByRole("row", { name: /month/ });
    expect(within(month).getByText("not counted")).toBeInTheDocument();
    const minute = within(table).getByRole("row", { name: /minute_of_day/ });
    expect(within(minute).getByText("Drifted")).toBeInTheDocument();
  });
  it("labels a forced retrain as a demo in the retrain log", async () => {
    renderWith(json(MODEL), json({ ...MONITORING, retrains: [RETRAIN] }));
    const log = await screen.findByRole("list", { name: /retrain decisions/i });
    expect(within(log).getByText(/forced demo/i)).toBeInTheDocument();
    expect(within(log).getByText(/Rejected/)).toBeInTheDocument();
    expect(within(log).getByText(/kept v1/)).toBeInTheDocument();
  });
  it("gives every chart a data table alternative", async () => {
    renderWith(json(MODEL), json(MONITORING));
    await screen.findByRole("img", { name: /Brier score per day/ });
    const tables = screen.getAllByRole("table", { name: /per day \(table\)/i, hidden: true });
    expect(tables).toHaveLength(4);
    const [brier] = tables;
    expect(brier).toBeDefined();
    expect(within(brier as HTMLElement).getAllByRole("row", { hidden: true })).toHaveLength(1 + 5);
  });
  it("explains unreadable monitoring results", async () => {
    const problem = {
      type: "/errors/monitoring-unavailable",
      title: "Monitoring results are temporarily unavailable",
      status: 503,
      detail: "",
      instance: "/api/v1/monitoring",
      request_id: "r",
    };
    renderWith(json(MODEL), json(problem, 503));
    expect(await screen.findByText("Couldn't load daily monitoring.")).toBeInTheDocument();
  });
});
