// Mirror of services/api/app/schemas.py; keep in sync (rules.md §4).
export type RiskLevel = "low" | "medium" | "high";
export type Direction = "up" | "down";

export interface Station {
  eva: string;
  name: string;
  state: string;
}
export interface Factor {
  feature: string;
  direction: Direction;
  text: string;
}
export interface Prediction {
  p_late: number;
  risk_level: RiskLevel;
  top_factors: Factor[];
}
export interface Train {
  type: string;
  number: string | null;
  line: string | null;
  destination: string | null;
  /** Stations after this one (live data, Phase 7); absent or empty for sample data. */
  via?: string[];
}
export interface Departure {
  event_id: string;
  planned_departure: string;
  live_departure: string | null;
  live_delay_min: number | null;
  cancelled: boolean;
  platform: string | null;
  train: Train;
  prediction: Prediction | null;
}
export interface DeparturesResponse {
  station: { eva: string; name: string };
  data_as_of: string;
  stale: boolean;
  data_source: "sample" | "live";
  replayed_from: string | null;
  model_version: string | null;
  departures: Departure[];
}
export interface ModelInfo {
  model_name: string;
  version: string;
  previous_version: string | null;
  trained_at: string;
  data_snapshot_id: string;
  git_sha: string;
  train_window: { start: string; end: string };
  metrics: {
    test_brier: number | null;
    test_auc: number | null;
    test_pr_auc: number | null;
    test_log_loss: number | null;
    test_ece: number | null;
    baseline_brier: number | null;
  };
  risk_thresholds: { medium: number; high: number };
}
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
  request_id: string;
}

// Phase 9: GET /api/v1/monitoring (dbdelay.serving.monitoring.MonitoringView).
export type DayStatus = "ok" | "no_data" | "too_few_rows";
export interface Verdict {
  drift_bad: boolean;
  performance_bad: boolean;
  drift_share_max: number;
  brier_ratio_max: number;
}
export interface HistoryEntry {
  day: string;
  status: DayStatus;
  model_version: string;
  rows: number;
  brier: number | null;
  roc_auc: number | null;
  ece: number | null;
  late_rate: number | null;
  mean_p_late: number | null;
  brier_ratio: number | null;
  drift_share: number | null;
  prediction_drift: number | null;
  verdict: Verdict;
}
export interface ColumnDrift {
  column: string;
  method: "wasserstein_normed" | "jensen_shannon";
  score: number;
  drifted: boolean;
  counted: boolean;
}
export interface DailyResult {
  day: string;
  status: DayStatus;
  model_version: string;
  champion_test_brier: number;
  performance: {
    rows: number;
    late_rate: number | null;
    mean_p_late: number | null;
    brier: number | null;
    log_loss: number | null;
    roc_auc: number | null;
    ece: number | null;
  };
  brier_ratio: number | null;
  drift: {
    columns: ColumnDrift[];
    drift_share: number;
    prediction: ColumnDrift;
    reference_rows: number;
    current_rows: number;
    day_type: "weekday" | "weekend_or_holiday";
  } | null;
  verdict: Verdict;
  excluded_rows: Record<string, number>;
  computed_at: string;
}
export interface RetrainRecord {
  request_id: string;
  reason: string;
  forced: boolean;
  requested_at: string;
  decided_at: string;
  decision: "released" | "rejected" | "failed";
  challenger_version: string | null;
  champion_before: string | null;
  champion_after: string | null;
  failed_checks: string[];
}
export interface Monitoring {
  latest: DailyResult | null;
  history: HistoryEntry[];
  retrains: RetrainRecord[];
}
