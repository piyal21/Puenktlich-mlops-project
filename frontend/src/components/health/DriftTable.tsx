import type { DailyResult } from "../../api/types";

const METHOD = { wasserstein_normed: "Wasserstein (normed)", jensen_shannon: "Jensen–Shannon" };

export function DriftTable({ drift }: { drift: NonNullable<DailyResult["drift"]> }) {
  const rows = [...drift.columns].sort(
    (a, b) => Number(b.counted) - Number(a.counted) || b.score - a.score,
  );
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface p-4 shadow-card">
      <table aria-label="Drift per model input" className="w-full text-left text-sm tabular">
        <caption className="mb-2 text-left text-muted">
          Yesterday vs the model&apos;s training sample (
          {drift.day_type === "weekday" ? "weekdays" : "weekends and holidays"}). Score ≥ 0.1 =
          drifted. Calendar inputs always differ on a single day, so they are not counted.
        </caption>
        <thead>
          <tr className="border-b border-border">
            <th scope="col" className="py-1.5 font-medium">
              Input
            </th>
            <th scope="col" className="py-1.5 font-medium">
              Score
            </th>
            <th scope="col" className="py-1.5 font-medium">
              Test
            </th>
            <th scope="col" className="py-1.5 font-medium">
              Result
            </th>
          </tr>
        </thead>
        <tbody>
          {[...rows, drift.prediction].map((c) => (
            <tr key={c.column} className="border-b border-border last:border-0">
              <th scope="row" className="py-1.5 font-normal">
                {c.column === "p_late" ? "p_late (predictions)" : c.column}
              </th>
              <td className="py-1.5">{c.score.toFixed(3)}</td>
              <td className="py-1.5 text-muted">{METHOD[c.method]}</td>
              <td className="py-1.5">
                {c.drifted ? "Drifted" : "Stable"}
                {c.counted ? null : (
                  <>
                    {" · "}
                    <span className="text-muted">not counted</span>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
