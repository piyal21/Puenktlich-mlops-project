import { CircleAlert, CircleCheck, Clock, RefreshCw } from "lucide-react";
import type { Monitoring } from "../../api/types";
import { formatDate } from "../../lib/format";
import type { HealthState } from "../../lib/monitoring";

const LOOK = {
  ok: { label: "OK", icon: CircleCheck, tone: "bg-risk-low-soft text-risk-low" },
  watch: { label: "Watch", icon: CircleAlert, tone: "bg-risk-medium-soft text-risk-medium" },
  retrain: {
    label: "Retrain requested",
    icon: RefreshCw,
    tone: "bg-risk-high-soft text-risk-high",
  },
  late: { label: "Monitor late", icon: Clock, tone: "bg-risk-high-soft text-risk-high" },
} as const;

const EXPLAIN = {
  ok: "Yesterday's real outcomes match the model's test quality, and its inputs look as usual.",
  watch:
    "At least one of the last 3 days was worse than the limits. 3 bad days in a row request a retrain.",
  retrain:
    "3 bad days in a row: a retrain was requested. A new model must pass the same quality gate.",
  late: "The daily check has not run for more than 2 days, so these numbers are old.",
} as const;

export function MonitoringStatus({
  monitoring,
  state,
}: {
  monitoring: Monitoring;
  state: Exclude<HealthState, "none">;
}) {
  const look = LOOK[state];
  const Icon = look.icon;
  const newest = monitoring.history.at(-1);
  if (!newest) return null;
  return (
    <div
      role="status"
      aria-label="Monitoring status"
      className="flex flex-wrap items-start gap-3 rounded-lg border border-border bg-surface p-4 shadow-card"
    >
      <span
        className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold ${look.tone}`}
      >
        <Icon aria-hidden="true" className="size-4" />
        {look.label}
      </span>
      <div className="min-w-0 flex-1 text-sm">
        <p>
          Last checked day <strong>{formatDate(newest.day)}</strong> · model v{newest.model_version}{" "}
          · {newest.rows.toLocaleString("en-GB")} trains with a known outcome
        </p>
        <p className="mt-1 text-muted">{EXPLAIN[state]}</p>
      </div>
    </div>
  );
}
