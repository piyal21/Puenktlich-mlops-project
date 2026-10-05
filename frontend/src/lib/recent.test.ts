import { describe, expect, it, vi } from "vitest";
import { MAX_RECENT, readRecent, rememberStation } from "./recent";

const station = (n: number) => ({ eva: `800000${n}`, name: `Station ${n}`, state: "BY" });

describe("recent stations", () => {
  it("keeps the newest five without duplicates", () => {
    for (let i = 0; i < 7; i++) rememberStation(station(i));
    rememberStation(station(4));
    const recent = readRecent();
    expect(recent).toHaveLength(MAX_RECENT);
    expect(recent[0]?.eva).toBe("8000004");
    expect(new Set(recent.map((s) => s.eva)).size).toBe(MAX_RECENT);
  });
  it("survives blocked or corrupt storage", () => {
    localStorage.setItem("puenktlich.recentStations", "{not json");
    expect(readRecent()).toEqual([]);
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(rememberStation(station(1))).toHaveLength(1);
    spy.mockRestore();
  });
});
