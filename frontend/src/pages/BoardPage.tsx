import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import type { Departure, Station } from "../api/types";
import { DepartureBoard } from "../components/DepartureBoard";
import { DepartureDetail } from "../components/DepartureDetail";
import { StationSearch } from "../components/StationSearch";
import { useDepartures } from "../hooks/useDepartures";
import { useModel } from "../hooks/useModel";
import { useStations } from "../hooks/useStations";
import { formatTime } from "../lib/format";
import { readRecent, rememberStation } from "../lib/recent";

const HOURS = [1, 3, 6] as const;
const TITLE = "text-[28px] leading-9 font-[650] sm:text-[32px] sm:leading-10";

export function BoardPage() {
  const { eva } = useParams();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [hours, setHours] = useState<number>(3);
  const [recent, setRecent] = useState<Station[]>(() => readRecent());
  const [selected, setSelected] = useState<Departure | null>(null);
  const stations = useStations(query);
  const board = useDepartures(eva, hours);
  const model = useModel();

  function select(station: Station) {
    setRecent(rememberStation(station));
    setQuery("");
    setSelected(null);
    void navigate(`/station/${station.eva}`);
  }

  const data = board.data;
  return (
    <div className="space-y-4">
      <StationSearch
        query={query}
        onQueryChange={setQuery}
        options={stations.data ?? []}
        onSelect={select}
      />
      {recent.length > 0 ? (
        <ul aria-label="Recent stations" className="flex flex-wrap gap-2">
          {recent.map((station) => (
            <li key={station.eva}>
              <button
                type="button"
                onClick={() => select(station)}
                className="min-h-11 rounded-sm bg-surface-2 px-3 text-sm hover:bg-primary-soft"
              >
                <span lang="de">{station.name}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {eva === undefined ? (
        <>
          <h1 className={TITLE}>Will my train leave on time?</h1>
          <p className="text-muted">
            Pick a station to see the next departures and their chance of leaving 6+ minutes late.
          </p>
        </>
      ) : (
        <>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className={TITLE}>
                <span lang="de">{data?.station.name ?? "Departures"}</span>
              </h1>
              {data ? (
                <p className="flex items-center gap-2 text-sm text-muted">
                  <span
                    aria-hidden="true"
                    className={`size-2 rounded-full ${
                      data.data_source === "live" ? "bg-accent" : "bg-muted"
                    }`}
                  />
                  {data.data_source === "live" ? "Live" : "Sample"} · Updated{" "}
                  {formatTime(data.data_as_of)}
                </p>
              ) : null}
            </div>
            <div
              role="group"
              aria-label="Time window"
              className="flex gap-1 rounded-md bg-surface-2 p-1"
            >
              {HOURS.map((h) => (
                <button
                  key={h}
                  type="button"
                  aria-pressed={hours === h}
                  onClick={() => setHours(h)}
                  className={`min-h-11 min-w-11 rounded-sm px-3 text-sm font-medium ${
                    hours === h ? "bg-surface text-text shadow-card" : "text-muted hover:text-text"
                  }`}
                >
                  {h} h
                </button>
              ))}
            </div>
          </div>
          <DepartureBoard
            data={data}
            isPending={board.isPending}
            error={board.error}
            hours={hours}
            onRetry={() => void board.refetch()}
            onShowMore={() => setHours(6)}
            onOpen={setSelected}
          />
        </>
      )}

      {selected && data ? (
        <DepartureDetail
          departure={selected}
          thresholds={model.data?.risk_thresholds ?? null}
          modelVersion={data.model_version}
          trainedAt={model.data?.trained_at ?? null}
          dataAsOf={data.data_as_of}
          onClose={() => setSelected(null)}
        />
      ) : null}
    </div>
  );
}
