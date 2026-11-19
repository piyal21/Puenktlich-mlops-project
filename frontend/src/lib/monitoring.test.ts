import { describe, expect, it } from "vitest";
import { MONITORING, historyEntry } from "../test/fixtures";
import { healthState } from "./monitoring";

const bad = { drift_bad: false, performance_bad: true, drift_share_max: 0.3, brier_ratio_max: 1.1 };

describe("healthState", () => {
  it("is none before the first run", () => {
    expect(healthState({ latest: null, history: [], retrains: [] }, "2026-11-19")).toBe("none");
  });
  it("is ok when the newest days are good and fresh", () => {
    expect(healthState(MONITORING, "2026-11-19")).toBe("ok");
  });
  it("is late when the newest day is more than 2 days old", () => {
    expect(healthState(MONITORING, "2026-11-21")).toBe("late");
  });
  it("is watch when one of the last 3 days is bad", () => {
    const history = [
      ...MONITORING.history.slice(0, -1),
      historyEntry("2026-11-18", { verdict: bad }),
    ];
    expect(healthState({ ...MONITORING, history }, "2026-11-19")).toBe("watch");
  });
  it("is retrain when the last 3 consecutive days are bad for the same reason", () => {
    const history = ["2026-11-16", "2026-11-17", "2026-11-18"].map((d) =>
      historyEntry(d, { verdict: bad }),
    );
    expect(healthState({ ...MONITORING, history }, "2026-11-19")).toBe("retrain");
  });
  it("is only watch when the bad days are not consecutive", () => {
    const history = ["2026-11-14", "2026-11-17", "2026-11-18"].map((d) =>
      historyEntry(d, { verdict: bad }),
    );
    expect(healthState({ ...MONITORING, history }, "2026-11-19")).toBe("watch");
  });
});
