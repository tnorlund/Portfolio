import fs from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";
import {
  colorizeRegions,
  createFocusTierMap,
  grayscaleAndDifference,
  selectMarkers,
  shiTomasiScores,
  sobelGradient,
  watershed,
} from "../components/home/Rotoscope/algorithm";
import {
  PORTRAIT_PROCESSING_SIZE,
  PORTRAIT_ROTOSCOPE_OPTIONS,
} from "../components/home/Rotoscope/portraitConfig";

/**
 * Rebuild the /rotoscope explainer stills from the current homepage pass.
 *
 *   npx tsx scripts/generate-rotoscope-explainer-stills.ts
 *
 * Every still is 960×720 from public/rotoscope-portrait.jpg and
 * PORTRAIT_ROTOSCOPE_OPTIONS. Also refreshes public/rotoscope-basins.webp so
 * the homepage no-JS fallback matches the article.
 */

const ROOT = path.join(__dirname, "..");
const SOURCE = path.join(ROOT, "public/rotoscope-portrait.jpg");
const PUBLIC = path.join(ROOT, "public");

const FACE_RGB = [30, 136, 229] as const;
const BODY_RGB = [251, 140, 0] as const;
const BACKGROUND_RGB = [109, 109, 109] as const;

const grayToRgba = (gray: Uint8Array, width: number, height: number): Uint8ClampedArray => {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let index = 0, offset = 0; index < gray.length; index += 1, offset += 4) {
    const value = gray[index];
    pixels[offset] = value;
    pixels[offset + 1] = value;
    pixels[offset + 2] = value;
    pixels[offset + 3] = 255;
  }
  return pixels;
};

const stretchToByte = (values: ArrayLike<number>): Uint8Array => {
  const count = values.length;
  const sorted = Float32Array.from(values);
  sorted.sort();
  const low = sorted[Math.floor((count - 1) * 0.02)] ?? 0;
  const high = sorted[Math.floor((count - 1) * 0.98)] ?? 1;
  const span = Math.max(1e-6, high - low);
  const output = new Uint8Array(count);
  for (let index = 0; index < count; index += 1) {
    const unit = Math.min(1, Math.max(0, (values[index] - low) / span));
    output[index] = Math.round(unit * 255);
  }
  return output;
};

const mixByte = (base: number, tint: number, amount: number): number =>
  Math.round(base * (1 - amount) + tint * amount);

const writeWebp = async (
  name: string,
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  quality = 78,
): Promise<void> => {
  const file = path.join(PUBLIC, name);
  await sharp(Buffer.from(pixels.buffer, pixels.byteOffset, pixels.byteLength), {
    raw: { width, height, channels: 4 },
  })
    .webp({ quality, effort: 5 })
    .toFile(file);
  process.stdout.write(`${file}\n`);
};

const overlayMarkers = (
  gray: Uint8Array,
  width: number,
  height: number,
  markerIndices: Uint32Array,
  focusTiers: Uint8Array,
): Uint8ClampedArray => {
  const pixels = grayToRgba(gray, width, height);
  for (let offset = 0; offset < pixels.length; offset += 4) {
    const lifted = Math.min(255, Math.round(pixels[offset] * 0.42 + 150));
    pixels[offset] = lifted;
    pixels[offset + 1] = lifted;
    pixels[offset + 2] = lifted;
  }
  const tintFor = (tier: number): readonly [number, number, number] => {
    if (tier === 0) return FACE_RGB;
    if (tier === 1) return BODY_RGB;
    return BACKGROUND_RGB;
  };
  for (const index of markerIndices) {
    const y = Math.floor(index / width);
    const x = index - y * width;
    const [red, green, blue] = tintFor(focusTiers[index]);
    for (let dy = -1; dy <= 1; dy += 1) {
      for (let dx = -1; dx <= 1; dx += 1) {
        const px = x + dx;
        const py = y + dy;
        if (px < 0 || py < 0 || px >= width || py >= height) continue;
        const offset = (py * width + px) * 4;
        pixels[offset] = red;
        pixels[offset + 1] = green;
        pixels[offset + 2] = blue;
      }
    }
  }
  return pixels;
};

const overlayFocus = (
  gray: Uint8Array,
  width: number,
  height: number,
  focusTiers: Uint8Array,
): Uint8ClampedArray => {
  const pixels = grayToRgba(gray, width, height);
  for (let index = 0, offset = 0; index < focusTiers.length; index += 1, offset += 4) {
    const base = Math.min(255, Math.round(gray[index] * 0.55 + 70));
    const tint =
      focusTiers[index] === 0 ? FACE_RGB : focusTiers[index] === 1 ? BODY_RGB : BACKGROUND_RGB;
    const amount = focusTiers[index] === 2 ? 0.38 : 0.58;
    pixels[offset] = mixByte(base, tint[0], amount);
    pixels[offset + 1] = mixByte(base, tint[1], amount);
    pixels[offset + 2] = mixByte(base, tint[2], amount);
    pixels[offset + 3] = 255;
  }
  return pixels;
};

const snapshotFlood = (
  gradient: Uint8Array,
  width: number,
  height: number,
  markerIndices: Uint32Array,
  painted: Uint8ClampedArray,
  fractions: readonly number[],
): Uint8ClampedArray[] => {
  const count = width * height;
  const labels = new Uint32Array(count);
  const queued = new Uint8Array(count);
  const next = new Int32Array(count);
  next.fill(-1);
  const heads = new Int32Array(256);
  const tails = new Int32Array(256);
  heads.fill(-1);
  tails.fill(-1);
  const enqueue = (index: number, level: number): void => {
    if (heads[level] < 0) heads[level] = index;
    else next[tails[level]] = index;
    tails[level] = index;
  };

  let regionCount = 0;
  for (let marker = 0; marker < markerIndices.length; marker += 1) {
    const index = markerIndices[marker];
    if (index >= count || queued[index] !== 0) continue;
    regionCount += 1;
    labels[index] = regionCount;
    queued[index] = 1;
    enqueue(index, 0);
  }

  const neighborX = [-1, 0, 1, -1, 1, -1, 0, 1] as const;
  const neighborY = [-1, -1, -1, 0, 0, 1, 1, 1] as const;
  const targets = fractions.map((fraction) => Math.floor(count * fraction));
  const paper = Uint8Array.from({ length: count }, (_, index) => {
    const offset = index * 4;
    const luma = (painted[offset] + painted[offset + 1] + painted[offset + 2]) / 3;
    return Math.min(255, Math.round(luma * 0.18 + 210));
  });
  const paintFrame = (): Uint8ClampedArray => {
    const frame = grayToRgba(paper, width, height);
    for (let index = 0, offset = 0; index < count; index += 1, offset += 4) {
      if (queued[index] === 0 || labels[index] === 0) continue;
      frame[offset] = painted[offset];
      frame[offset + 1] = painted[offset + 1];
      frame[offset + 2] = painted[offset + 2];
    }
    return frame;
  };

  let level = 0;
  let visited = regionCount;
  let nextTarget = 0;
  const frames: Uint8ClampedArray[] = [];
  while (visited < count && nextTarget < targets.length) {
    while (level < 256 && heads[level] < 0) level += 1;
    if (level >= 256) break;
    const index = heads[level];
    heads[level] = next[index];
    if (heads[level] < 0) tails[level] = -1;
    next[index] = -1;
    const y = Math.floor(index / width);
    const x = index - y * width;
    const label = labels[index];
    for (let neighbor = 0; neighbor < 8; neighbor += 1) {
      const nx = x + neighborX[neighbor];
      const ny = y + neighborY[neighbor];
      if (nx < 0 || nx >= width || ny < 0 || ny >= height) continue;
      const nextIndex = ny * width + nx;
      if (queued[nextIndex] !== 0) continue;
      queued[nextIndex] = 1;
      labels[nextIndex] = label;
      visited += 1;
      enqueue(nextIndex, Math.max(level, gradient[nextIndex]));
      if (nextTarget < targets.length && visited >= targets[nextTarget]) {
        frames.push(paintFrame());
        nextTarget += 1;
      }
    }
  }
  while (frames.length < fractions.length) frames.push(paintFrame());
  return frames;
};

const main = async (): Promise<void> => {
  const { width, height } = PORTRAIT_PROCESSING_SIZE;
  const { data, info } = await sharp(SOURCE)
    .resize(width, height, { fit: "fill" })
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  if (info.width !== width || info.height !== height) {
    throw new Error(`unexpected resize ${info.width}x${info.height}`);
  }
  const source = new Uint8ClampedArray(
    data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength),
  );
  const options = PORTRAIT_ROTOSCOPE_OPTIONS;
  const { gray, blurred, difference } = grayscaleAndDifference(
    source,
    width,
    height,
    options.blurRadius ?? 3,
  );
  const scores = shiTomasiScores(difference, width, height);
  const scoreHeat = stretchToByte(
    Float32Array.from(scores, (value) => Math.log1p(Math.max(0, value))),
  );
  const watershedGradient = sobelGradient(gray, width, height).magnitude;
  const focusTiers = createFocusTierMap(width, height, options.focus!);
  const selected = selectMarkers(scores, width, height, options);
  const segmented = watershed(watershedGradient, width, height, selected.indices);
  const painted = colorizeRegions(
    source,
    segmented.labels,
    width,
    height,
    segmented.regionCount,
  );
  const floodFrames = snapshotFlood(
    watershedGradient,
    width,
    height,
    selected.indices,
    painted,
    [0.12, 0.32, 0.55, 0.78],
  );

  await fs.mkdir(PUBLIC, { recursive: true });
  await writeWebp("rotoscope-gray.webp", grayToRgba(gray, width, height), width, height);
  await writeWebp("rotoscope-blurred.webp", grayToRgba(blurred, width, height), width, height);
  await writeWebp(
    "rotoscope-difference.webp",
    grayToRgba(stretchToByte(difference), width, height),
    width,
    height,
    62,
  );
  await writeWebp(
    "rotoscope-shi-tomasi.webp",
    grayToRgba(scoreHeat, width, height),
    width,
    height,
    62,
  );
  await writeWebp(
    "rotoscope-watershed-gradient.webp",
    grayToRgba(stretchToByte(watershedGradient), width, height),
    width,
    height,
    62,
  );
  await writeWebp("rotoscope-focus.webp", overlayFocus(gray, width, height, focusTiers), width, height);
  await writeWebp(
    "rotoscope-markers.webp",
    overlayMarkers(gray, width, height, selected.indices, focusTiers),
    width,
    height,
  );
  const basinsPath = path.join(PUBLIC, "rotoscope-basins.webp");
  const basinsGray = Buffer.alloc(width * height);
  const paintedPixels = painted;
  const colorAt = (index: number): number => {
    const offset = index * 4;
    return (
      (paintedPixels[offset] << 16) |
      (paintedPixels[offset + 1] << 8) |
      paintedPixels[offset + 2]
    );
  };
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x;
      const color = colorAt(index);
      const edge =
        (x > 0 && colorAt(index - 1) !== color) ||
        (x + 1 < width && colorAt(index + 1) !== color) ||
        (y > 0 && colorAt(index - width) !== color) ||
        (y + 1 < height && colorAt(index + width) !== color);
      basinsGray[index] = edge ? 0 : 221;
    }
  }
  await sharp(basinsGray, { raw: { width, height, channels: 1 } })
    .webp({ lossless: true })
    .toFile(basinsPath);
  process.stdout.write(`${basinsPath}\n`);
  await writeWebp("rotoscope-painted.webp", painted, width, height);
  await writeWebp("rotoscope-flood-1.webp", floodFrames[0], width, height);
  await writeWebp("rotoscope-flood-2.webp", floodFrames[1], width, height);
  await writeWebp("rotoscope-flood-3.webp", floodFrames[2], width, height);
  await writeWebp("rotoscope-flood-4.webp", floodFrames[3], width, height);
};

void main();
