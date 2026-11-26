import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";

/** Wait this long after the last keystroke before searching. The API throttle is one bucket
 * for the whole site (2 req/s, security review S1), so one request per keystroke would use it up. */
export const SEARCH_DEBOUNCE_MS = 250;

function useDebounced(value: string, delayMs: number): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return settled;
}

export function useStations(query: string) {
  const q = useDebounced(query.trim(), SEARCH_DEBOUNCE_MS);
  return useQuery({
    queryKey: ["stations", q],
    queryFn: ({ signal }) => api.stations(q, signal),
    staleTime: 60 * 60 * 1000,
    placeholderData: keepPreviousData,
  });
}
