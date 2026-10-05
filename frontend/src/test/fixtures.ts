import type { Departure, DeparturesResponse, ModelInfo } from "../api/types";

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
