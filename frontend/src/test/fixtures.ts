import type {
  DailyResult,
  Departure,
  DeparturesResponse,
  HistoryEntry,
  ModelInfo,
  Monitoring,
  RetrainRecord,
} from "../api/types";

export function makeDeparture(overrides: Partial<Departure> = {}): Departure {
  return {
    event_id: "e1",
    planned_departure: "2026-10-05T17:42:00+02:00",
    live_departure: "2026-10-05T17:42:00+02:00",
    live_delay_min: 0,
    cancelled: false,
    platform: "3",
    train: { type: "RE", number: "4711", line: "RE1", destination: "Göttingen" },
    prediction: {
      p_late: 0.34,
      risk_level: "medium",
      top_factors: [
        { feature: "stop_index", direction: "up", text: "Late stop in a long journey" },
        { feature: "weekday", direction: "up", text: "Fridays are busier" },
        {
          feature: "hour_local",
          direction: "down",
          text: "Departures around 17:00 are usually on time",
        },
      ],
    },
    ...overrides,
  };
}

export function makeBoard(overrides: Partial<DeparturesResponse> = {}): DeparturesResponse {
  return {
    station: { eva: "8010101", name: "Erfurt Hbf" },
    data_as_of: "2026-10-05T15:30:00+02:00",
    stale: false,
    data_source: "live",
    replayed_from: null,
    model_version: "1",
    departures: [makeDeparture(), makeDeparture({ event_id: "e2" })],
    ...overrides,
  };
}

// Real champion v1 values (models/1/metrics.json, Phase 4).
export const MODEL: ModelInfo = {
  model_name: "puenktlich-delay",
  version: "1",
  previous_version: null,
  trained_at: "2026-10-04T00:30:20.750306Z",
  data_snapshot_id: "2026-08-31_38b45c7a",
  git_sha: "84059fa",
  train_window: { start: "2025-12-01", end: "2026-08-03" },
  metrics: {
    test_brier: 0.14079126856356794,
    test_auc: 0.8077911556913215,
    test_pr_auc: 0.5958589182652804,
    test_log_loss: 0.4390725331874017,
    test_ece: 0.021795552668577318,
    baseline_brier: 0.15203213243127522,
  },
  risk_thresholds: { medium: 0.2, high: 0.45 },
};

// Phase 9 monitoring fixtures: made-up test values, not measurements.
const OK_VERDICT = {
  drift_bad: false,
  performance_bad: false,
  drift_share_max: 0.3,
  brier_ratio_max: 1.1,
};

export function historyEntry(day: string, overrides: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    day,
    status: "ok",
    model_version: "1",
    rows: 12000,
    brier: 0.15,
    roc_auc: 0.79,
    ece: 0.02,
    late_rate: 0.22,
    mean_p_late: 0.19,
    brier_ratio: 1.07,
    drift_share: 1 / 7,
    prediction_drift: 0.04,
    verdict: OK_VERDICT,
    ...overrides,
  };
}

export const DAILY: DailyResult = {
  day: "2026-11-18",
  status: "ok",
  model_version: "1",
  champion_test_brier: 0.1408,
  performance: {
    rows: 12000,
    late_rate: 0.22,
    mean_p_late: 0.19,
    brier: 0.15,
    log_loss: 0.47,
    roc_auc: 0.79,
    ece: 0.02,
  },
  brier_ratio: 1.07,
  drift: {
    columns: [
      { column: "eva", method: "jensen_shannon", score: 0.03, drifted: false, counted: true },
      {
        column: "minute_of_day",
        method: "wasserstein_normed",
        score: 0.12,
        drifted: true,
        counted: true,
      },
      { column: "month", method: "jensen_shannon", score: 1, drifted: true, counted: false },
    ],
    drift_share: 1 / 7,
    prediction: {
      column: "p_late",
      method: "wasserstein_normed",
      score: 0.04,
      drifted: false,
      counted: false,
    },
    reference_rows: 36000,
    current_rows: 12000,
    day_type: "weekday",
  },
  verdict: OK_VERDICT,
  excluded_rows: { cancelled: 150, gap_hour: 0, gap_station_day: 40 },
  computed_at: "2026-11-19T03:30:12Z",
};

export const RETRAIN: RetrainRecord = {
  request_id: "20261120T033100Z-performance",
  reason: "performance",
  forced: true,
  requested_at: "2026-11-20T03:31:00Z",
  decided_at: "2026-11-20T09:00:00Z",
  decision: "rejected",
  challenger_version: "2",
  champion_before: "1",
  champion_after: "1",
  failed_checks: ["brier_vs_champion"],
};

export const MONITORING: Monitoring = {
  latest: DAILY,
  history: ["2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18"].map((d) =>
    historyEntry(d),
  ),
  retrains: [],
};
