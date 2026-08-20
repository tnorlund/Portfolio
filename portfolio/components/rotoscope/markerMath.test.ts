import {
  classifyFocusTier,
  minimumEigenvalue,
  selectMarkers,
  shiTomasiScores,
} from "../home/Rotoscope/algorithm";
import { PORTRAIT_ROTOSCOPE_OPTIONS } from "../home/Rotoscope/portraitConfig";
import {
  localMaxAt,
  prepareMarkerFields,
  shiTomasiAt,
} from "./markerMath";

const rgba = (
  width: number,
  height: number,
  pixel: (x: number, y: number) => readonly [number, number, number, number],
): Uint8ClampedArray => {
  const output = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      output.set(pixel(x, y), (y * width + x) * 4);
    }
  }
  return output;
};

test("shiTomasiAt matches the engine score at an interior pixel", () => {
  const source = rgba(9, 9, (x) => {
    const value = x * 20;
    return [value, value, value, 255];
  });
  const fields = prepareMarkerFields(
    { width: 9, height: 9, rgba: source },
    1,
    PORTRAIT_ROTOSCOPE_OPTIONS,
  );
  const sample = shiTomasiAt(fields, 4, 4);
  const engine = shiTomasiScores(fields.difference, 9, 9);
  expect(sample.score).toBe(engine[4 * 9 + 4]);
  expect(sample.gxWindow).toHaveLength(3);
  expect(sample.gyWindow[1]).toHaveLength(3);
  expect(minimumEigenvalue(sample.xx, sample.xy, sample.yy)).toBe(sample.score);
});

test("localMaxAt keeps a unique peak and rejects a stronger neighbor", () => {
  const scores = new Float32Array(25);
  scores[12] = 10;
  scores[11] = 3;
  expect(localMaxAt(scores, 5, 5, 2, 2)).toMatchObject({
    kept: true,
    winner: null,
  });

  scores[7] = 12;
  const rejected = localMaxAt(scores, 5, 5, 2, 2);
  expect(rejected.kept).toBe(false);
  expect(rejected.winner).toEqual({ dx: 0, dy: -1 });
});

test("equal scores prefer the smaller index, matching the engine", () => {
  const scores = new Float32Array(25);
  scores[12] = 8;
  scores[11] = 8;
  const sample = localMaxAt(scores, 5, 5, 2, 2);
  expect(sample.kept).toBe(false);
  expect(sample.winner).toEqual({ dx: -1, dy: 0 });
});

test("prepareMarkerFields markers match selectMarkers", () => {
  const source = rgba(9, 9, (x, y) => [x * 24, y * 12, 40, 255]);
  const fields = prepareMarkerFields(
    { width: 9, height: 9, rgba: source },
    1,
    { ...PORTRAIT_ROTOSCOPE_OPTIONS, markerBudget: 6 },
  );
  const selected = selectMarkers(fields.scores, 9, 9, {
    ...PORTRAIT_ROTOSCOPE_OPTIONS,
    markerBudget: 6,
  });
  expect(fields.markerSet.size).toBe(selected.indices.length);
  expect(classifyFocusTier(4, 4, 9, 9, PORTRAIT_ROTOSCOPE_OPTIONS.focus!)).toMatch(
    /face|body|background/,
  );
  for (const index of selected.indices) {
    expect(fields.markerSet.has(index)).toBe(true);
  }
});
