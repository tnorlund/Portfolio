import {
  grayscaleAndDifference,
  sobelGradient,
} from "../home/Rotoscope/algorithm";
import {
  neighborhood,
  preparePixelFields,
  rec601Gray,
  sampleRgba,
  sobelAt,
} from "./pixelMath";

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

test("Rec. 601 gray matches the engine integer weights", () => {
  expect(rec601Gray(20, 40, 60)).toBe(36);
  expect(rec601Gray(0, 0, 0)).toBe(0);
  expect(rec601Gray(255, 255, 255)).toBe(255);
});

test("preparePixelFields matches grayscaleAndDifference", () => {
  const source = rgba(5, 3, (x, y) => [x * 20, y * 30, 40, 255]);
  const expected = grayscaleAndDifference(source, 5, 3, 2);
  const fields = preparePixelFields({ width: 5, height: 3, rgba: source }, 2);
  expect(Array.from(fields.gray)).toEqual(Array.from(expected.gray));
  expect(Array.from(fields.blurred)).toEqual(Array.from(expected.blurred));
  expect(Array.from(fields.difference)).toEqual(Array.from(expected.difference));
});

test("neighborhood is a clamped (2r+1) window around the sample", () => {
  const source = rgba(5, 5, () => [10, 10, 10, 255]);
  const fields = preparePixelFields({ width: 5, height: 5, rgba: source }, 1);
  expect(neighborhood(fields.gray, 5, 5, 0, 0, 1)).toHaveLength(3);
  expect(neighborhood(fields.gray, 5, 5, 0, 0, 1)[0]).toHaveLength(3);
  expect(neighborhood(fields.gray, 5, 5, 2, 2, 2)).toHaveLength(5);
});

test("sobelAt matches the engine magnitude at an interior pixel", () => {
  const source = rgba(9, 9, (x) => {
    const value = x * 20;
    return [value, value, value, 255];
  });
  const fields = preparePixelFields({ width: 9, height: 9, rgba: source }, 1);
  const sample = sobelAt(fields.gray, fields.graySobel, 9, 9, 4, 4);
  const engine = sobelGradient(fields.gray, 9, 9);
  expect(sample.gx).toBe(engine.x[4 * 9 + 4]);
  expect(sample.gy).toBe(engine.y[4 * 9 + 4]);
  expect(sample.magnitude).toBe(engine.magnitude[4 * 9 + 4]);
  expect(sample.window).toHaveLength(3);
  expect(sample.window[0]).toHaveLength(3);
});

test("sampleRgba clamps to the image", () => {
  const source = {
    width: 2,
    height: 1,
    rgba: Uint8ClampedArray.from([1, 2, 3, 255, 4, 5, 6, 255]),
  };
  expect(sampleRgba(source, -3, 9)).toEqual([1, 2, 3]);
  expect(sampleRgba(source, 99, 0)).toEqual([4, 5, 6]);
});
