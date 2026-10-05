import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export const BOARD_REFRESH_MS = 60_000;

/** Last good data stays in `data` when a refetch fails (shown with an error banner). */
export function useDepartures(eva: string | undefined, hours: number) {
  return useQuery({
    queryKey: ["departures", eva, hours],
    queryFn: ({ signal }) => api.departures(eva ?? "", hours, signal),
    enabled: eva !== undefined,
    refetchInterval: BOARD_REFRESH_MS,
    retry: 1,
  });
}
