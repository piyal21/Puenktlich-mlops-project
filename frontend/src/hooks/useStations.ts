import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export function useStations(query: string) {
  const q = query.trim();
  return useQuery({
    queryKey: ["stations", q],
    queryFn: ({ signal }) => api.stations(q, signal),
    staleTime: 60 * 60 * 1000,
    placeholderData: keepPreviousData,
  });
}
