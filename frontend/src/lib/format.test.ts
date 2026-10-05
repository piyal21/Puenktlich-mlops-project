import { describe, expect, it } from "vitest";
import { formatDate, formatTime, minutesOld, percent } from "./format";

describe("format", () => {
  it("shows 24-hour Berlin time", () => {
    expect(formatTime("2026-10-05T15:42:00+02:00")).toBe("15:42");
    expect(formatTime("2026-12-07T13:05:00Z")).toBe("14:05");
  });
  it("shows dates in Berlin", () => {
    expect(formatDate("2026-08-24")).toBe("24 Aug 2026");
  });
  it("rounds probabilities to whole percent", () => {
    expect(percent(0.344)).toBe("34%");
    expect(percent(0.996)).toBe("100%");
  });
  it("counts whole minutes since a time", () => {
    expect(minutesOld("2026-10-05T13:00:00Z", new Date("2026-10-05T13:27:30Z"))).toBe(27);
    expect(minutesOld("2026-10-05T13:00:00Z", new Date("2026-10-05T12:00:00Z"))).toBe(0);
  });
});
