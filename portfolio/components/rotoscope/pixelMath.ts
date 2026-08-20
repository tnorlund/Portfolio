import {
  grayscaleAndDifference,
  sobelGradient,
  type GradientStage,
} from "../home/Rotoscope/algorithm";

export const REC601 = { r: 77, g: 150, b: 29, bias: 128 } as const;

export const rec601Gray = (red: number, green: number, blue: number): number =>
  (REC601.r * red + REC601.g * green + REC601.b * blue + REC601.bias) >> 8;

export const SOBEL_X = [
  [-1, 0, 1],
  [-2, 0, 2],
  [-1, 0, 1],
] as const;

export const SOBEL_Y = [
  [-1, -2, -1],
  [0, 0, 0],
  [1, 2, 1],
] as const;

export interface RgbaBuffer {
  width: number;
  height: number;
  rgba: Uint8ClampedArray;
}

export interface PixelFields {
  width: number;
  height: number;
  rgba: Uint8ClampedArray;
  gray: Uint8Array;
  blurred: Uint8Array;
  difference: Uint8Array;
  graySobel: GradientStage;
  differenceSobel: GradientStage;
}

export const clampCoord = (value: number, max: number): number =>
  Math.min(max, Math.max(0, value));

export const sampleRgba = (
  source: RgbaBuffer,
  x: number,
  y: number,
): readonly [number, number, number] => {
  const px = clampCoord(Math.floor(x), source.width - 1);
  const py = clampCoord(Math.floor(y), source.height - 1);
  const offset = (py * source.width + px) * 4;
  return [source.rgba[offset], source.rgba[offset + 1], source.rgba[offset + 2]];
};

export const preparePixelFields = (
  source: RgbaBuffer,
  blurRadius: number,
): PixelFields => {
  const stage = grayscaleAndDifference(
    source.rgba,
    source.width,
    source.height,
    blurRadius,
  );
  return {
    width: source.width,
    height: source.height,
    rgba: source.rgba,
    gray: stage.gray,
    blurred: stage.blurred,
    difference: stage.difference,
    graySobel: sobelGradient(stage.gray, source.width, source.height),
    differenceSobel: sobelGradient(stage.difference, source.width, source.height),
  };
};

export const sampleField = (
  field: Uint8Array,
  width: number,
  height: number,
  x: number,
  y: number,
): number => {
  const px = clampCoord(Math.floor(x), width - 1);
  const py = clampCoord(Math.floor(y), height - 1);
  return field[py * width + px];
};

export const neighborhood = (
  field: Uint8Array,
  width: number,
  height: number,
  x: number,
  y: number,
  radius: number,
): number[][] => {
  const cx = clampCoord(Math.floor(x), width - 1);
  const cy = clampCoord(Math.floor(y), height - 1);
  const cells: number[][] = [];
  for (let dy = -radius; dy <= radius; dy += 1) {
    const row: number[] = [];
    for (let dx = -radius; dx <= radius; dx += 1) {
      row.push(
        sampleField(field, width, height, cx + dx, cy + dy),
      );
    }
    cells.push(row);
  }
  return cells;
};

export interface SobelSample {
  gx: number;
  gy: number;
  magnitude: number;
  window: number[][];
}

export const sobelAt = (
  field: Uint8Array,
  gradients: GradientStage,
  width: number,
  height: number,
  x: number,
  y: number,
): SobelSample => {
  const window = neighborhood(field, width, height, x, y, 1);
  const cx = clampCoord(Math.floor(x), width - 1);
  const cy = clampCoord(Math.floor(y), height - 1);
  const index = cy * width + cx;
  return {
    gx: gradients.x[index],
    gy: gradients.y[index],
    magnitude: gradients.magnitude[index],
    window,
  };
};
