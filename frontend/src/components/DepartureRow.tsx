import type { Departure } from "../api/types";
import { formatTime } from "../lib/format";
import { RiskBadge } from "./RiskBadge";

// The live time is shown only for delayed trains; an on-time copy of the planned time is noise.
function delayClass(delay: number): string {
  return delay >= 6 ? "text-risk-high" : "text-risk-medium";
}

export function DepartureRow({
  departure,
  onOpen,
}: {
  departure: Departure;
  onOpen: (departure: Departure) => void;
}) {
  const { train, cancelled, live_departure: live } = departure;
  const delay = departure.live_delay_min ?? 0;
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(departure)}
        className={`grid w-full grid-cols-[auto_1fr_auto] items-start gap-3 rounded-lg border border-border bg-surface p-3 text-left shadow-card transition-colors duration-150 hover:bg-surface-2 ${
          cancelled ? "opacity-70" : ""
        }`}
      >
        <span className="font-mono tabular">
          <span className={`block text-xl font-medium ${cancelled ? "line-through" : ""}`}>
            {formatTime(departure.planned_departure)}
          </span>
          {!cancelled && live && delay > 0 ? (
            <span className={`block text-sm ${delayClass(delay)}`}>
              {formatTime(live)} +{delay}
            </span>
          ) : null}
        </span>
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-2 text-sm">
            <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-xs font-medium">
              {train.type} <span className="font-mono">{train.number ?? ""}</span>
            </span>
            {train.line ? <span className="text-muted">{train.line}</span> : null}
          </span>
          <span className="block truncate font-semibold">
            <span aria-hidden="true">→ </span>
            <span lang="de">{train.destination ?? "Destination unknown"}</span>
          </span>
          {departure.platform ? (
            <span className="block text-sm text-muted">Pl. {departure.platform}</span>
          ) : null}
        </span>
        <RiskBadge prediction={departure.prediction} cancelled={cancelled} />
      </button>
    </li>
  );
}
