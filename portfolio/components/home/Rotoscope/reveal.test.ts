import {
  applyBasinRevealPhase,
  basinRevealActForPhase,
  createBasinRevealMap,
} from "./reveal";
import type { FocusGeometry } from "./algorithm";

const focus: FocusGeometry = {
  face: {
    centerX: 1.5 / 7,
    centerY: 1.5 / 3,
    radiusX: 0.3,
    radiusY: 0.5,
  },
  body: [
    [0.38, 0],
    [0.76, 0],
    [0.76, 1],
    [0.38, 1],
  ],
};

const fixture = (): Uint8ClampedArray => {
  const colors = [
    0, 0, 0, 1, 1, 2, 2,
    0, 0, 0, 1, 1, 2, 2,
    0, 0, 0, 3, 3, 2, 2,
  ];
  const pixels = new Uint8ClampedArray(colors.length * 4);
  for (let index = 0; index < colors.length; index += 1) {
    const value = colors[index] * 50;
    pixels.set([value, value + 1, value + 2, 100 + index], index * 4);
  }
  return pixels;
};

test("stages connected basins by tier and grows each one from its own center", () => {
  const first = createBasinRevealMap(fixture(), 7, 3, focus, 36);
  const replay = createBasinRevealMap(fixture(), 7, 3, focus, 36);

  expect(first.basinCount).toBe(4);
  expect(Array.from(replay.phases)).toEqual(Array.from(first.phases));
  // Face basin A starts at its interior and expands toward its corners.
  expect(first.phases[8]).toBeLessThan(first.phases[0]);
  // Basin B is in the body phase; basin C is background and starts last.
  expect(first.phases[10]).toBeLessThan(first.phases[6]);
  expect(Array.from(first.phases)).toEqual([
    9, 6, 9, 8, 15, 26, 29,
    6, 0, 6, 15, 18, 19, 26,
    9, 6, 9, 15, 24, 26, 29,
  ]);
});

test("uses Vision semantics for a strict features, subject, background sequence", () => {
  const result = createBasinRevealMap(fixture(), 7, 3, focus, 36, {
    primaryFeatures: [
      {
        centerX: 1.5 / 7,
        centerY: 1.5 / 3,
        radiusX: 0.2,
        radiusY: 0.45,
        order: 0,
      },
    ],
    personMask: {
      width: 7,
      height: 3,
      pixels: Uint8Array.from([
        1, 1, 1, 1, 1, 0, 0,
        1, 1, 1, 1, 1, 0, 0,
        1, 1, 1, 1, 1, 0, 0,
      ]),
    },
  });

  const basinPhases = (indices: number[]) => indices.map((index) => result.phases[index]);
  const features = basinPhases([0, 1, 2, 7, 8, 9, 14, 15, 16]);
  const subject = basinPhases([3, 4, 10, 11, 17, 18]);
  const background = basinPhases([5, 6, 12, 13, 19, 20]);

  expect(Math.max(...features)).toBeLessThan(Math.min(...subject));
  expect(Math.max(...subject)).toBeLessThan(Math.min(...background));
  expect(basinRevealActForPhase(Math.max(...features), 36)).toBe("features");
  expect(basinRevealActForPhase(Math.min(...subject), 36)).toBe("subject");
  expect(basinRevealActForPhase(Math.min(...background), 36)).toBe("background");
});

test("reveal frames are monotonic and finish byte-identical to the result", () => {
  const source = fixture();
  const reveal = createBasinRevealMap(source, 7, 3, focus, 36);
  const target = new Uint8ClampedArray(source.length);

  applyBasinRevealPhase(target, source, reveal.phases, -1, 8);
  const earlyAlpha = Array.from(
    { length: reveal.phases.length },
    (_, index) => target[index * 4 + 3],
  );
  expect(earlyAlpha.some((alpha) => alpha > 0)).toBe(true);
  expect(earlyAlpha.some((alpha) => alpha === 0)).toBe(true);

  applyBasinRevealPhase(target, source, reveal.phases, 8, 35);
  expect(Array.from(target)).toEqual(Array.from(source));
});

test("alpha does not split a basin and diagonal contact does not merge it", () => {
  const pixels = new Uint8ClampedArray([
    20, 30, 40, 10, 90, 100, 110, 255,
    90, 100, 110, 25, 20, 30, 40, 240,
  ]);
  const result = createBasinRevealMap(pixels, 2, 2, focus, 8);
  expect(result.basinCount).toBe(4);

  const one = createBasinRevealMap(
    new Uint8ClampedArray([1, 2, 3, 4]),
    1,
    1,
    focus,
    8,
  );
  expect(Array.from(one.phases)).toEqual([0]);
});
