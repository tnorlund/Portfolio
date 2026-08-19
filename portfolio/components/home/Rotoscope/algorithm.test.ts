import {
  classifyFocusTier,
  colorizeRegions,
  DEFAULT_ROTOSCOPE_OPTIONS,
  grayscaleAndDifference,
  minimumEigenvalue,
  runRotoscope,
  selectMarkers,
  shiTomasiScores,
  sobelGradient,
  validateRgba,
  watershed,
} from "./algorithm";

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

test("validates dimensions and the exact RGBA byte length", () => {
  expect(validateRgba(new Uint8ClampedArray(16), 2, 2)).toBe(4);
  expect(() => validateRgba(new Uint8ClampedArray(15), 2, 2)).toThrow(
    "RGBA byte length",
  );
  expect(() => validateRgba(new Uint8ClampedArray(0), 0, 1)).toThrow(
    "dimensions",
  );
});

test("single-image difference is zero for a flat image and preserves grayscale", () => {
  const source = rgba(5, 3, () => [20, 40, 60, 77]);
  const stage = grayscaleAndDifference(source, 5, 3, 2);

  expect(new Set(stage.gray)).toEqual(new Set([36]));
  expect(Array.from(stage.difference)).toEqual(new Array(15).fill(0));
});

test("single-image blur and difference match the exact edge fixture", () => {
  const source = rgba(3, 1, (x) => {
    const value = x * 100;
    return [value, value, value, 255];
  });
  const stage = grayscaleAndDifference(source, 3, 1, 1);

  expect(Array.from(stage.gray)).toEqual([0, 100, 200]);
  expect(Array.from(stage.difference)).toEqual([50, 0, 50]);
});

test("non-finite public blur input uses the documented default", () => {
  const source = rgba(5, 3, (x, y) => [x * 20, y * 30, 40, 255]);
  const invalid = grayscaleAndDifference(source, 5, 3, Number.NaN);
  const expected = grayscaleAndDifference(
    source,
    5,
    3,
    DEFAULT_ROTOSCOPE_OPTIONS.blurRadius,
  );
  expect(Array.from(invalid.difference)).toEqual(Array.from(expected.difference));
});

test("analytical minimum eigenvalue matches the closed-form diagonal cases", () => {
  expect(minimumEigenvalue(9, 0, 4)).toBeCloseTo(4);
  expect(minimumEigenvalue(9, 3, 9)).toBeCloseTo(6);
  expect(minimumEigenvalue(0, 0, 0)).toBe(0);
});

test("Sobel and Shi-Tomasi stages produce bounded borders and corner response", () => {
  const width = 9;
  const height = 9;
  const source = new Uint8Array(width * height);
  for (let y = 4; y < height; y += 1) {
    for (let x = 4; x < width; x += 1) source[y * width + x] = 255;
  }
  const gradient = sobelGradient(source, width, height);
  const scores = shiTomasiScores(source, width, height);

  expect(gradient.magnitude[0]).toBe(0);
  expect([
    gradient.x[30],
    gradient.y[30],
    gradient.magnitude[30],
    gradient.x[40],
    gradient.y[40],
    gradient.magnitude[40],
  ]).toEqual([255, 255, 128, 765, 765, 255]);
  expect([scores[30], scores[31], scores[40], scores[49]]).toEqual([
    260100,
    869552.1875,
    2340900,
    1530487.375,
  ]);
  expect(scores[0]).toBe(0);
});

test("focus geometry classifies face before body and leaves a background tier", () => {
  const focus = DEFAULT_ROTOSCOPE_OPTIONS.focus;
  expect(classifyFocusTier(39, 55, 100, 100, focus)).toBe("face");
  expect(classifyFocusTier(35, 85, 100, 100, focus)).toBe("body");
  expect(classifyFocusTier(95, 10, 100, 100, focus)).toBe("background");
});

test("marker selection honors explicit tier budgets when geometry has capacity", () => {
  const width = 80;
  const height = 60;
  const scores = new Float32Array(width * height);
  for (let index = 0; index < scores.length; index += 1) {
    scores[index] = ((index * 7919) % 997) + 1;
  }
  const selected = selectMarkers(scores, width, height, {
    markerBudget: 20,
    quotas: { face: 0.5, body: 0.3, background: 0.2 },
    spacing: { face: 1, body: 1, background: 1 },
  });

  expect(selected.tierCounts).toEqual({ face: 10, body: 6, background: 4 });
  expect(new Set(selected.indices).size).toBe(selected.indices.length);
});

test("marker allocation never exceeds tiny or adversarial budgets", () => {
  const scores = new Float32Array(100).map((_, index) => index + 1);
  const options = {
    markerBudget: 1,
    quotas: { face: 0.5, body: 0.5, background: 0 },
    spacing: { face: 1, body: 1, background: 1 },
  };
  const selected = selectMarkers(scores, 10, 10, options);
  expect(selected.indices.length).toBeLessThanOrEqual(1);

  const negative = selectMarkers(scores, 10, 10, {
    ...options,
    markerBudget: 4,
    quotas: { face: 1, body: -10, background: 1 },
  });
  expect(negative.indices.length).toBeLessThanOrEqual(4);
  expect(negative.tierCounts.body).toBe(0);
});

test("marker ranking and tie order match the exact golden fixture", () => {
  const scores = new Float32Array(25);
  scores[6] = 10;
  scores[18] = 9;
  const selected = selectMarkers(scores, 5, 5, {
    markerBudget: 2,
    quotas: { face: 1, body: 0, background: 0 },
    spacing: { face: 1, body: 1, background: 1 },
    focus: {
      face: { centerX: 0.5, centerY: 0.5, radiusX: 2, radiusY: 2 },
      body: [],
    },
  });
  expect(Array.from(selected.indices)).toEqual([6, 18]);
});

test("watershed is deterministic, eight-connected, and labels every pixel", () => {
  const width = 3;
  const height = 3;
  const gradient = new Uint8Array([
    0, 255, 0,
    255, 255, 255,
    0, 255, 0,
  ]);
  const markers = Uint32Array.from([0, 8]);
  const first = watershed(gradient, width, height, markers);
  const second = watershed(gradient, width, height, markers);

  expect(Array.from(first.labels)).toEqual(Array.from(second.labels));
  expect(Array.from(first.labels)).toEqual([
    1, 1, 1,
    1, 1, 2,
    1, 2, 2,
  ]);
  expect(first.regionCount).toBe(2);
  expect(Array.from(first.labels).every((label) => label > 0)).toBe(true);
  // The center is reachable diagonally from the first marker at the same level.
  expect(first.labels[4]).toBe(1);
});

test("watershed handles 1x1 and an empty marker list", () => {
  const result = watershed(new Uint8Array([0]), 1, 1, new Uint32Array(0));
  expect(result.regionCount).toBe(1);
  expect(Array.from(result.labels)).toEqual([1]);
});

test("region colorization uses source RGB means and preserves alpha", () => {
  const source = new Uint8ClampedArray([
    10, 20, 30, 40,
    30, 40, 50, 60,
    100, 110, 120, 130,
  ]);
  const output = colorizeRegions(
    source,
    Uint32Array.from([1, 1, 2]),
    3,
    1,
    2,
  );
  expect(Array.from(output)).toEqual([
    20, 30, 40, 40,
    20, 30, 40, 60,
    100, 110, 120, 130,
  ]);
});

test("full single-image pipeline is reproducible on an odd-sized fixture", () => {
  const width = 17;
  const height = 13;
  const source = rgba(width, height, (x, y) => [
    (x * 17 + y * 3) % 256,
    (x * 5 + y * 19) % 256,
    (x * 11 + y * 7) % 256,
    (x + y) % 2 === 0 ? 255 : 180,
  ]);
  const options = {
    blurRadius: 2,
    markerBudget: 12,
    spacing: { face: 1, body: 1, background: 1 },
  };
  const first = runRotoscope(source, width, height, options);
  const second = runRotoscope(source, width, height, options);

  expect(first.mode).toBe("single-image");
  expect(first.markerCount).toBeGreaterThan(0);
  expect(Array.from(first.pixels)).toEqual(Array.from(second.pixels));
  for (let index = 3; index < source.length; index += 4) {
    expect(first.pixels[index]).toBe(source[index]);
  }
});

test("full flat pipeline matches the exact one-region golden output", () => {
  const source = rgba(3, 1, (x) => {
    const value = x * 100;
    return [value, value, value, 100 + x];
  });
  const result = runRotoscope(source, 3, 1, {
    blurRadius: 1,
    markerBudget: 1,
  });
  expect(result.markerCount).toBe(1);
  expect(Array.from(result.pixels)).toEqual([
    100, 100, 100, 100,
    100, 100, 100, 101,
    100, 100, 100, 102,
  ]);
});
