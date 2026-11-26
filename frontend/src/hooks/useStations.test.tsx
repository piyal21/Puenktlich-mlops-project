import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SEARCH_DEBOUNCE_MS, useStations } from "./useStations";

afterEach(() => {
  vi.unstubAllGlobals();
});

function setup() {
  const fetchMock = vi.fn<(input: RequestInfo | URL) => Promise<Response>>(() =>
    Promise.resolve(new Response("[]", { status: 200 })),
  );
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const urls = () => fetchMock.mock.calls.map(([input]) => String(input));
  return { wrapper, urls };
}

describe("useStations", () => {
  it("searches once after typing stops, not on every keystroke", async () => {
    // Security review S1: the API throttle is one bucket for the whole site (2 req/s).
    const { wrapper, urls } = setup();
    const { rerender } = renderHook(({ q }) => useStations(q), {
      wrapper,
      initialProps: { q: "" },
    });
    await waitFor(() => expect(urls()).toEqual(["/api/v1/stations?q="]));

    rerender({ q: "F" });
    rerender({ q: "Fr" });
    rerender({ q: "Fra" });

    await waitFor(() => expect(urls()).toEqual(["/api/v1/stations?q=", "/api/v1/stations?q=Fra"]));
    expect(SEARCH_DEBOUNCE_MS).toBe(250);
  });
});
