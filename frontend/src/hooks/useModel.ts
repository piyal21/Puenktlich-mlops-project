import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";

export function useModel() {
  return useQuery({
    queryKey: ["model"],
    queryFn: ({ signal }) => api.model(signal),
    staleTime: 5 * 60 * 1000,
    // 503 means no champion: a normal state, not worth retrying.
    retry: (count, error) => !(error instanceof ApiError && error.status === 503) && count < 2,
  });
}
