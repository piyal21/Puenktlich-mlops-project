import type { Station } from "../api/types";

const KEY = "puenktlich.recentStations";
export const MAX_RECENT = 5;

function isStation(value: unknown): value is Station {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return typeof record.eva === "string" && typeof record.name === "string";
}

export function readRecent(): Station[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(isStation).slice(0, MAX_RECENT) : [];
  } catch {
    return [];
  }
}

export function rememberStation(station: Station): Station[] {
  const next = [station, ...readRecent().filter((s) => s.eva !== station.eva)].slice(0, MAX_RECENT);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage blocked: recent stations last for this page view only.
  }
  return next;
}
