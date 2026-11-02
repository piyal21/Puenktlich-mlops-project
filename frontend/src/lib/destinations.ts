import type { Departure } from "../api/types";

/** Each final stop on the board once, in German alphabetical order. */
export function destinationsOf(departures: Departure[]): string[] {
  const names = new Set<string>();
  for (const { train } of departures) {
    if (train.destination !== null) names.add(train.destination);
  }
  return [...names].sort((a, b) => a.localeCompare(b, "de"));
}

/** The departures whose final stop is ``destination``; all of them when it is null. */
export function goingTo(departures: Departure[], destination: string | null): Departure[] {
  if (destination === null) return departures;
  return departures.filter((d) => d.train.destination === destination);
}
