import {
  PORTRAIT_PRIMARY_FEATURES,
  decodePortraitPersonMask,
} from "./portraitReveal";

test("decodes the versioned Vision person mask", () => {
  const mask = decodePortraitPersonMask({
    coordinateSpace: "normalized-top-left",
    width: 3,
    height: 2,
    runs: [0, 2, 1, 3, 0, 1],
  });

  expect(mask.width).toBe(3);
  expect(mask.height).toBe(2);
  expect(Array.from(mask.pixels)).toEqual([0, 0, 1, 1, 1, 0]);
});

test("rejects malformed Vision masks and keeps the four primary feature regions", () => {
  expect(() =>
    decodePortraitPersonMask({
      coordinateSpace: "normalized-top-left",
      width: 2,
      height: 2,
      runs: [1, 5],
    }),
  ).toThrow("invalid portrait person mask");
  expect(PORTRAIT_PRIMARY_FEATURES.map((feature) => feature.name)).toEqual([
    "left-eye",
    "right-eye",
    "nose",
    "mouth",
  ]);
});
