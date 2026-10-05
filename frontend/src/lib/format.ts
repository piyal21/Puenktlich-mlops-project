const BERLIN = "Europe/Berlin";
const TIME = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: BERLIN,
});
const DATE = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: BERLIN,
});

export const formatTime = (iso: string): string => TIME.format(new Date(iso));
export const formatDate = (iso: string): string => DATE.format(new Date(iso));
export const percent = (p: number): string => `${Math.round(p * 100)}%`;

export function minutesOld(iso: string, now: Date = new Date()): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000));
}
