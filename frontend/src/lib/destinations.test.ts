import { describe, expect, it } from "vitest";
import { makeDeparture } from "../test/fixtures";
import { destinationsOf, filterSummary, goingTo } from "./destinations";

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

describe("blank destinations (they occur in real data)", () => {
  it("are not offered", () => {
    expect(destinationsOf([to(""), to("   "), to("Erlangen")])).toEqual(["Erlangen"]);
  });

  it("padded names are offered once and still match", () => {
    const board = [to(" Erlangen ", "a"), to("Erlangen", "b")];
    expect(destinationsOf(board)).toEqual(["Erlangen"]);
    expect(goingTo(board, "Erlangen")).toHaveLength(2);
  });
});

describe("filterSummary", () => {
  const board = [to("München Hbf", "a"), to("München Hbf", "b"), to("Erlangen")];

  it("is empty without a choice", () => {
    expect(filterSummary(board, null, 3)).toBe("");
  });

  it("counts the matching trains", () => {
    expect(filterSummary(board, "München Hbf", 3)).toBe("2 of 3 trains go to München Hbf");
  });

  it("says when none match, in the window's words", () => {
    expect(filterSummary(board, "Bonn", 1)).toBe("No trains to Bonn in the next hour");
    expect(filterSummary(board, "Bonn", 6)).toBe("No trains to Bonn in the next 6 hours");
  });
});
