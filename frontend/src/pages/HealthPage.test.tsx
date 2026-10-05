import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MODEL } from "../test/fixtures";
import { HealthPage } from "./HealthPage";

function renderWith(response: Response) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <HealthPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HealthPage", () => {
  it("shows the champion's real test metrics", async () => {
    renderWith(new Response(JSON.stringify(MODEL), { status: 200 }));
    expect(await screen.findByText("v1")).toBeInTheDocument();
    expect(screen.getByText("0.141")).toBeInTheDocument();
    expect(screen.getByText("Baseline 0.152 · lower is better")).toBeInTheDocument();
    expect(screen.getByText("0.808")).toBeInTheDocument();
    expect(screen.getByText(/Daily monitoring starts in a later phase/)).toBeInTheDocument();
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
    renderWith(new Response(JSON.stringify(problem), { status: 503 }));
    expect(await screen.findByText("No forecast model is live right now.")).toBeInTheDocument();
  });
});
