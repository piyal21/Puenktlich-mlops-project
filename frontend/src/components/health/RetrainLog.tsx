import type { RetrainRecord } from "../../api/types";
import { formatDate } from "../../lib/format";

const DECISION = { released: "Released", rejected: "Rejected", failed: "Failed" } as const;

function outcome(r: RetrainRecord): string {
  if (r.decision === "released") return `now v${r.champion_after}`;
  return r.champion_after ? `kept v${r.champion_after}` : "no model";
}

export function RetrainLog({ retrains }: { retrains: RetrainRecord[] }) {
  if (retrains.length === 0) {
    return <p className="text-sm text-muted">No retrain has been requested yet.</p>;
  }
  return (
    <ul aria-label="Retrain decisions" className="space-y-2">
      {[...retrains].reverse().map((r) => (
        <li
          key={r.request_id}
          className="rounded-lg border border-border bg-surface p-3 text-sm shadow-card"
        >
          <p>
            <strong>{formatDate(r.decided_at)}</strong> · {r.reason}
            {r.forced ? " (forced demo)" : ""} · {DECISION[r.decision]}
            {r.challenger_version ? ` challenger v${r.challenger_version}` : ""} · {outcome(r)}
          </p>
          {r.failed_checks.length > 0 ? (
            <p className="mt-1 text-muted">Failed checks: {r.failed_checks.join(", ")}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
