import type { Departure } from "../api/types";

/** A train's final stop without padding; "" when unknown (blank names occur in real data). */
function finalStop(departure: Departure): string {
  return departure.train.destination?.trim() ?? "";
}

/** Each final stop on the board once, in German alphabetical order. */
export function destinationsOf(departures: Departure[]): string[] {
  const names = new Set(departures.map(finalStop));
  names.delete("");
  return [...names].sort((a, b) => a.localeCompare(b, "de"));
}

/** The departures whose final stop is ``destination``; all of them when it is null. */
export function goingTo(departures: Departure[], destination: string | null): Departure[] {
  if (destination === null) return departures;
  return departures.filter((d) => finalStop(d) === destination);
}

/** "hour" or "3 hours", for "in the next …". */
export function horizon(hours: number): string {
  return hours === 1 ? "hour" : `${hours} hours`;
}

/** What the filter did, for screen readers; "" when no destination is chosen. */
export function filterSummary(
  departures: Departure[],
  destination: string | null,
  hours: number,
): string {
  if (destination === null) return "";
  const matching = goingTo(departures, destination).length;
  return matching === 0
    ? `No trains to ${destination} in the next ${horizon(hours)}`
    : `${matching} of ${departures.length} trains go to ${destination}`;
}
