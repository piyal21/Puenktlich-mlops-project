import type { HistoryEntry } from "../../api/types";
import { TrendChart } from "./TrendChart";

const fixed = (digits: number) => (v: number) => v.toFixed(digits);
const pct = (v: number) => `${(v * 100).toFixed(0)}%`;

export function DailyCharts({
  history,
  championBrier,
  testAuc,
}: {
  history: HistoryEntry[];
  championBrier: number | null;
  testAuc: number | null;
}) {
  const days = history.map((e) => e.day);
  const limit = history.at(-1)?.verdict.brier_ratio_max ?? 1.1;
  const driftMax = history.at(-1)?.verdict.drift_share_max ?? 0.3;
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <TrendChart
        title="Brier score per day"
        days={days}
        series={[{ label: "Brier", color: "var(--series-1)", values: history.map((e) => e.brier) }]}
        reference={
          championBrier === null
            ? undefined
            : { label: `Limit ${limit.toFixed(2)}×`, value: championBrier * limit }
        }
        format={fixed(3)}
      />
      <TrendChart
        title="ROC-AUC per day"
        days={days}
        series={[
          { label: "ROC-AUC", color: "var(--series-1)", values: history.map((e) => e.roc_auc) },
        ]}
        reference={testAuc === null ? undefined : { label: "Test AUC", value: testAuc }}
        format={fixed(3)}
      />
      <TrendChart
        title="Drift share per day"
        days={days}
        series={[
          {
            label: "Drift share",
            color: "var(--series-1)",
            values: history.map((e) => e.drift_share),
          },
        ]}
        reference={{ label: `Limit ${driftMax.toFixed(2)}`, value: driftMax }}
        format={fixed(2)}
      />
      <TrendChart
        title="Late rate per day"
        days={days}
        series={[
          { label: "Actual", color: "var(--series-1)", values: history.map((e) => e.late_rate) },
          {
            label: "Predicted",
            color: "var(--series-2)",
            values: history.map((e) => e.mean_p_late),
          },
        ]}
        format={pct}
      />
    </div>
  );
}
