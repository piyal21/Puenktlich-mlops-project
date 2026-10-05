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
