import { describe, expect, it } from "vitest";
import { makeDeparture } from "../test/fixtures";
import { destinationsOf, goingTo } from "./destinations";

function to(destination: string | null, id = String(destination)) {
  return makeDeparture({
    event_id: id,
    train: { type: "RE", number: "1", line: null, destination },
  });
}

describe("destinationsOf", () => {
  it("lists each final stop once, in German alphabetical order", () => {
    const board = [
      to("München Hbf", "a"),
      to("Aachen Hbf"),
      to("München Hbf", "b"),
      to("Mainz Hbf"),
    ];
    expect(destinationsOf(board)).toEqual(["Aachen Hbf", "Mainz Hbf", "München Hbf"]);
  });

  it("skips trains without a known destination", () => {
    expect(destinationsOf([to(null), to("Erlangen")])).toEqual(["Erlangen"]);
  });
});

describe("goingTo", () => {
  const board = [to("München Hbf"), to("Erlangen"), to(null)];

  it("keeps only trains to that final stop", () => {
    expect(goingTo(board, "Erlangen").map((d) => d.train.destination)).toEqual(["Erlangen"]);
  });

  it("keeps everything when no destination is chosen", () => {
    expect(goingTo(board, null)).toHaveLength(3);
  });
});
