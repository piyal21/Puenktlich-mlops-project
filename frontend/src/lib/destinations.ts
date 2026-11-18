import type { Departure } from "../api/types";

/** Every stop a train still serves, trimmed, no blanks (blank names occur in real data). Live
 * data: `via` (its last entry is the real end, also when the route was shortened). Sample data
 * has no `via`, so only the final stop counts. */
export function placesOf(departure: Departure): string[] {
  const via = departure.train.via ?? [];
  const names = via.length > 0 ? via : [departure.train.destination ?? ""];
  return [...new Set(names.map((name) => name.trim()).filter((name) => name !== ""))];
}

/** Each place any train on the board goes to, once, in German alphabetical order. */
export function destinationsOf(departures: Departure[]): string[] {
  const names = new Set(departures.flatMap(placesOf));
  return [...names].sort((a, b) => a.localeCompare(b, "de"));
}

/** The departures that stop at ``destination`` on the way or end there; all when it is null. */
export function goingTo(departures: Departure[], destination: string | null): Departure[] {
  if (destination === null) return departures;
  return departures.filter((d) => placesOf(d).includes(destination));
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
