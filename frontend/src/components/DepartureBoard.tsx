import { TrainFront } from "lucide-react";
import type { Departure, DeparturesResponse } from "../api/types";
import { formatDate, minutesOld } from "../lib/format";
import { Banner } from "./Banner";
import { DepartureRow } from "./DepartureRow";

interface Props {
  data: DeparturesResponse | undefined;
  isPending: boolean;
  error: Error | null;
  hours: number;
  onRetry: () => void;
  onShowMore: () => void;
  onOpen: (departure: Departure) => void;
  now?: Date;
}

const BUTTON =
  "inline-flex min-h-11 items-center rounded-md px-4 text-sm font-medium transition-colors duration-150";

function Skeleton() {
  return (
    <div role="status" aria-label="Loading departures" aria-busy="true" className="space-y-2">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-[76px] animate-pulse rounded-lg bg-surface-2" />
      ))}
    </div>
  );
}

/** The board with every data state of design.md §8: loading, empty, error, stale, no model. */
export function DepartureBoard({
  data,
  isPending,
  error,
  hours,
  onRetry,
  onShowMore,
  onOpen,
  now = new Date(),
}: Props) {
  if (isPending && !data) return <Skeleton />;
  return (
    <div className="space-y-3">
      {error ? (
        <Banner
          variant="error"
          action={
            <button
              type="button"
              onClick={onRetry}
              className={`${BUTTON} border border-border bg-surface text-text`}
            >
              Try again
            </button>
          }
        >
          {data
            ? "Couldn't refresh the board. Showing the last data we have."
            : "Couldn't load departures."}
        </Banner>
      ) : null}
      {data ? (
        <>
          {data.stale ? (
            <Banner variant="stale">
              Live data is {minutesOld(data.data_as_of, now)} min old.
            </Banner>
          ) : null}
          {data.data_source === "sample" && data.replayed_from ? (
            <Banner variant="info">
              Sample data: real departures from {formatDate(data.replayed_from)} replayed onto
              today.
            </Banner>
          ) : null}
          {data.model_version === null ? (
            <Banner variant="info">Forecasts are temporarily unavailable.</Banner>
          ) : null}
          {data.departures.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-surface p-8 text-center">
              <TrainFront aria-hidden="true" className="size-8 text-muted" />
              <p>No departures in the next {hours === 1 ? "hour" : `${hours} hours`}</p>
              {hours < 6 ? (
                <button
                  type="button"
                  onClick={onShowMore}
                  className={`${BUTTON} bg-primary text-primary-fg hover:bg-primary-hover`}
                >
                  Show 6 hours
                </button>
              ) : null}
            </div>
          ) : (
            <ul aria-label={`Departures from ${data.station.name}`} className="space-y-2">
              {data.departures.map((departure) => (
                <DepartureRow key={departure.event_id} departure={departure} onOpen={onOpen} />
              ))}
            </ul>
          )}
        </>
      ) : null}
    </div>
  );
}
