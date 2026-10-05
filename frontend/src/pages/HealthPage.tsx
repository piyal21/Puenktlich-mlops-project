import { Activity } from "lucide-react";
import { ApiError } from "../api/client";
import type { ModelInfo } from "../api/types";
import { Banner } from "../components/Banner";
import { MetricCard } from "../components/MetricCard";
import { useModel } from "../hooks/useModel";
import { formatDate } from "../lib/format";

const num = (value: number | null, digits = 3) => (value === null ? "—" : value.toFixed(digits));

function ModelCards({ model }: { model: ModelInfo }) {
  const m = model.metrics;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <MetricCard
        label="Model version"
        value={`v${model.version}`}
        caption={`Trained ${formatDate(model.trained_at)}`}
      />
      <MetricCard
        label="Brier score (test)"
        value={num(m.test_brier)}
        caption={`Baseline ${num(m.baseline_brier)} · lower is better`}
      />
      <MetricCard label="ROC-AUC (test)" value={num(m.test_auc)} caption="Higher is better" />
      <MetricCard label="PR-AUC (test)" value={num(m.test_pr_auc)} caption="Higher is better" />
      <MetricCard
        label="Calibration error (ECE)"
        value={num(m.test_ece)}
        caption="Lower is better"
      />
      <MetricCard
        label="Training data"
        value={`${formatDate(model.train_window.start)} – ${formatDate(model.train_window.end)}`}
        caption={`Snapshot ${model.data_snapshot_id}`}
      />
    </div>
  );
}

export function HealthPage() {
  const model = useModel();
  const noModel = model.error instanceof ApiError && model.error.status === 503;
  return (
    <div className="space-y-6">
      <h1 className="text-[28px] leading-9 font-[650] sm:text-[32px] sm:leading-10">
        Model health
      </h1>
      {model.isPending ? (
        <div
          role="status"
          aria-label="Loading model details"
          aria-busy="true"
          className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
        >
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-lg bg-surface-2" />
          ))}
        </div>
      ) : noModel ? (
        <Banner variant="info">No forecast model is live right now.</Banner>
      ) : model.isError ? (
        <Banner
          variant="error"
          action={
            <button
              type="button"
              onClick={() => void model.refetch()}
              className="min-h-11 rounded-md border border-border bg-surface px-4 text-sm font-medium"
            >
              Try again
            </button>
          }
        >
          Couldn&apos;t load model details.
        </Banner>
      ) : (
        <ModelCards model={model.data} />
      )}
      <section aria-labelledby="monitoring" className="space-y-3">
        <h2 id="monitoring" className="text-xl font-semibold">
          Daily monitoring
        </h2>
        <div className="flex items-start gap-3 rounded-lg border border-border bg-surface p-4">
          <Activity aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-muted" />
          <p className="text-muted">
            Daily monitoring starts in a later phase: yesterday&apos;s accuracy and data drift will
            appear here. Until then, the numbers above come from the model&apos;s test period.
          </p>
        </div>
      </section>
    </div>
  );
}
