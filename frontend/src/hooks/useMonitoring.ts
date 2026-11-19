import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export function useMonitoring() {
  return useQuery({
    queryKey: ["monitoring"],
    queryFn: ({ signal }) => api.monitoring(signal),
    // Results change once a day; the API caches 5 min.
    staleTime: 5 * 60 * 1000,
  });
}
