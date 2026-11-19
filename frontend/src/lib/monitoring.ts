import type { HistoryEntry, Monitoring } from "../api/types";

/** Overall state on the health page (spec §4.6); "retrain" mirrors the backend trigger. */
export type HealthState = "none" | "ok" | "watch" | "retrain" | "late";

const STREAK = 3;
const LATE_AFTER_DAYS = 2;
const DAY_MS = 86_400_000;

function dayNumber(isoDay: string): number {
  return Math.round(Date.parse(`${isoDay}T00:00:00Z`) / DAY_MS);
}

function isBad(e: HistoryEntry): boolean {
  return e.verdict.drift_bad || e.verdict.performance_bad;
}

function isStreak(entries: HistoryEntry[]): boolean {
  if (entries.length < STREAK) return false;
  let previous: HistoryEntry | null = null;
  let consecutive = true;
  for (const e of entries) {
    const follows =
      previous === null ||
      (dayNumber(e.day) === dayNumber(previous.day) + 1 &&
        e.model_version === previous.model_version);
    consecutive = consecutive && e.status === "ok" && follows;
    previous = e;
  }
  return (
    consecutive &&
    (entries.every((e) => e.verdict.drift_bad) || entries.every((e) => e.verdict.performance_bad))
  );
}

/** `today` is the UTC date (YYYY-MM-DD) the page is viewed on. */
export function healthState(m: Monitoring, today: string): HealthState {
  const newest = m.history.at(-1);
  if (!newest) return "none";
  if (dayNumber(today) - dayNumber(newest.day) > LATE_AFTER_DAYS) return "late";
  const last = m.history.slice(-STREAK);
  if (isStreak(last)) return "retrain";
  return last.some(isBad) ? "watch" : "ok";
}
