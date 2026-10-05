import type { Prediction } from "../api/types";

const FILL = { low: "bg-risk-low", medium: "bg-risk-medium", high: "bg-risk-high" } as const;

export function ProbabilityBar({
  prediction,
  thresholds,
}: {
  prediction: Prediction;
  thresholds: { medium: number; high: number } | null;
}) {
  const pct = Math.round(prediction.p_late * 100);
  const ticks = thresholds ? [thresholds.medium, thresholds.high] : [];
  return (
    <div>
      <div
        role="meter"
        aria-label="Chance of leaving 6 or more minutes late"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={`${pct} percent`}
        className="relative h-3 rounded-full bg-surface-2"
      >
        <div
          className={`h-3 rounded-full ${FILL[prediction.risk_level]}`}
          style={{ width: `${pct}%` }}
        />
        {ticks.map((t) => (
          <div
            key={t}
            aria-hidden="true"
            className="absolute -top-1 h-5 w-0.5 bg-text"
            style={{ left: `${t * 100}%` }}
          />
        ))}
      </div>
      {ticks.length > 0 ? (
        <div aria-hidden="true" className="relative mt-1 h-4 text-xs text-muted">
          {ticks.map((t) => (
            <span key={t} className="absolute -translate-x-1/2" style={{ left: `${t * 100}%` }}>
              {Math.round(t * 100)}%
            </span>
          ))}
        </div>
      ) : null}
      <p className="mt-3 text-base">{pct}% chance of leaving 6+ minutes late</p>
    </div>
  );
}
