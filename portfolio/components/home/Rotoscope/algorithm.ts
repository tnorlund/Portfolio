/**
 * Scalar reference for the browser rotoscope.
 *
 * The 2017 MATLAB implementation and paper are the semantic source of truth:
 * background difference -> Shi-Tomasi features -> marker-controlled watershed
 * -> one mean source color per region. This browser adaptation changes only the
 * first stage: a blurred copy of a single still stands in for the clean
 * background frame used by the paper.
 *
 * The stage structure is canonical, while two numerical kernels are deliberate
 * browser approximations: an integer 3x3 Sobel replaces the MATLAB Gaussian
 * derivative at sigma 0.6, and a 3x3 tensor window replaces its 7x7 window.
 * Those bounded changes materially reduce work at display resolution and are
 * frozen by golden fixtures below the stage APIs.
 *
 * This implementation intentionally favors readability and deterministic test
 * fixtures. The WebAssembly implementation is required to match its observable
 * stage behavior.
 */

export const MAX_ROTOSCOPE_DIMENSION = 2048;
export const MAX_ROTOSCOPE_PIXELS = 1024 * 1024;

export type FocusTierName = "face" | "body" | "background";

export type NormalizedPoint = readonly [x: number, y: number];

export interface FocusGeometry {
  face: {
    centerX: number;
    centerY: number;
    radiusX: number;
    radiusY: number;
  };
  /** Polygon points in normalized image coordinates. */
  body: readonly NormalizedPoint[];
}

export interface TierValues {
  face: number;
  body: number;
  background: number;
}

export interface RotoscopeOptions {
  /** Radius of the low-frequency box blur used by single-image mode. */
  blurRadius: number;
  markerBudget: number;
  /** Fractional marker allocation. Values are normalized at runtime. */
  quotas: TierValues;
  /** Manhattan suppression radius per focus tier. */
  spacing: TierValues;
  focus: FocusGeometry;
}

export interface DifferenceStage {
  gray: Uint8Array;
  blurred: Uint8Array;
  difference: Uint8Array;
}

export interface GradientStage {
  x: Int16Array;
  y: Int16Array;
  magnitude: Uint8Array;
}

export interface SelectedMarkers {
  indices: Uint32Array;
  tierCounts: Record<FocusTierName, number>;
}

export interface WatershedResult {
  labels: Uint32Array;
  regionCount: number;
}

export interface RotoscopeResult {
  pixels: Uint8ClampedArray;
  markerCount: number;
  tierCounts: Record<FocusTierName, number>;
  mode: "single-image";
}

const DEFAULT_FOCUS: FocusGeometry = {
  face: {
    centerX: 0.4,
    centerY: 0.56,
    radiusX: 0.14,
    radiusY: 0.27,
  },
  body: [
    [0.31, 0.67],
    [0.53, 0.66],
    [0.75, 0.82],
    [0.8, 1],
    [0, 1],
    [0, 0.84],
    [0.26, 0.72],
  ],
};

export const DEFAULT_ROTOSCOPE_OPTIONS: Readonly<RotoscopeOptions> = {
  blurRadius: 9,
  markerBudget: 480,
  quotas: { face: 0.5, body: 0.3, background: 0.2 },
  spacing: { face: 3, body: 5, background: 8 },
  focus: DEFAULT_FOCUS,
};

const clamp = (value: number, low: number, high: number): number =>
  Math.min(high, Math.max(low, value));

const finiteOr = (value: number, fallback: number): number =>
  Number.isFinite(value) ? value : fallback;

const checkedPixelCount = (width: number, height: number): number => {
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width <= 0 ||
    height <= 0 ||
    width > MAX_ROTOSCOPE_DIMENSION ||
    height > MAX_ROTOSCOPE_DIMENSION
  ) {
    throw new RangeError("invalid rotoscope dimensions");
  }
  const count = width * height;
  if (!Number.isSafeInteger(count) || count > MAX_ROTOSCOPE_PIXELS) {
    throw new RangeError("rotoscope image exceeds the pixel limit");
  }
  return count;
};

export const validateRgba = (
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
): number => {
  const count = checkedPixelCount(width, height);
  if (pixels.length !== count * 4) {
    throw new RangeError("RGBA byte length does not match the dimensions");
  }
  return count;
};

export const normalizeRotoscopeOptions = (
  options: Partial<RotoscopeOptions>,
  pixelCount: number,
): RotoscopeOptions => {
  const quotas = options.quotas ?? DEFAULT_ROTOSCOPE_OPTIONS.quotas;
  const spacing = options.spacing ?? DEFAULT_ROTOSCOPE_OPTIONS.spacing;
  const suppliedFocus = options.focus ?? DEFAULT_ROTOSCOPE_OPTIONS.focus;
  const defaultFace = DEFAULT_ROTOSCOPE_OPTIONS.focus.face;
  const focus: FocusGeometry = {
    face: {
      centerX: clamp(finiteOr(suppliedFocus.face?.centerX, defaultFace.centerX), -4, 4),
      centerY: clamp(finiteOr(suppliedFocus.face?.centerY, defaultFace.centerY), -4, 4),
      radiusX: clamp(finiteOr(suppliedFocus.face?.radiusX, defaultFace.radiusX), 0.0001, 4),
      radiusY: clamp(finiteOr(suppliedFocus.face?.radiusY, defaultFace.radiusY), 0.0001, 4),
    },
    body: suppliedFocus.body.slice(0, 64).map(([x, y]) => [
      clamp(finiteOr(x, 0), -4, 4),
      clamp(finiteOr(y, 0), -4, 4),
    ]),
  };
  let faceQuota = Math.max(0, finiteOr(quotas.face, 0));
  let bodyQuota = Math.max(0, finiteOr(quotas.body, 0));
  let backgroundQuota = Math.max(0, finiteOr(quotas.background, 0));
  let quotaSum = faceQuota + bodyQuota + backgroundQuota;
  if (quotaSum <= 0) {
    faceQuota = DEFAULT_ROTOSCOPE_OPTIONS.quotas.face;
    bodyQuota = DEFAULT_ROTOSCOPE_OPTIONS.quotas.body;
    backgroundQuota = DEFAULT_ROTOSCOPE_OPTIONS.quotas.background;
    quotaSum = faceQuota + bodyQuota + backgroundQuota;
  }

  return {
    blurRadius: clamp(
      Math.round(
        finiteOr(options.blurRadius ?? DEFAULT_ROTOSCOPE_OPTIONS.blurRadius, 9),
      ),
      1,
      64,
    ),
    markerBudget: clamp(
      Math.round(
        finiteOr(
          options.markerBudget ?? DEFAULT_ROTOSCOPE_OPTIONS.markerBudget,
          480,
        ),
      ),
      1,
      pixelCount,
    ),
    quotas: {
      face: faceQuota / quotaSum,
      body: bodyQuota / quotaSum,
      background: backgroundQuota / quotaSum,
    },
    spacing: {
      face: clamp(Math.round(finiteOr(spacing.face, 3)), 1, 64),
      body: clamp(Math.round(finiteOr(spacing.body, 5)), 1, 64),
      background: clamp(
        Math.round(finiteOr(spacing.background, 8)),
        1,
        64,
      ),
    },
    focus,
  };
};

const boxBlur = (
  source: Uint8Array,
  width: number,
  height: number,
  radius: number,
): Uint8Array => {
  const count = width * height;
  const horizontal = new Uint16Array(count);
  const output = new Uint8Array(count);

  for (let y = 0; y < height; y += 1) {
    const row = y * width;
    let sum = 0;
    let right = Math.min(width - 1, radius);
    for (let x = 0; x <= right; x += 1) sum += source[row + x];

    for (let x = 0; x < width; x += 1) {
      const left = Math.max(0, x - radius);
      right = Math.min(width - 1, x + radius);
      horizontal[row + x] = Math.round(sum / (right - left + 1));
      const remove = x - radius;
      const add = x + radius + 1;
      if (remove >= 0) sum -= source[row + remove];
      if (add < width) sum += source[row + add];
    }
  }

  for (let x = 0; x < width; x += 1) {
    let sum = 0;
    let bottom = Math.min(height - 1, radius);
    for (let y = 0; y <= bottom; y += 1) sum += horizontal[y * width + x];

    for (let y = 0; y < height; y += 1) {
      const top = Math.max(0, y - radius);
      bottom = Math.min(height - 1, y + radius);
      output[y * width + x] = Math.round(sum / (bottom - top + 1));
      const remove = y - radius;
      const add = y + radius + 1;
      if (remove >= 0) sum -= horizontal[remove * width + x];
      if (add < height) sum += horizontal[add * width + x];
    }
  }

  return output;
};

export const grayscaleAndDifference = (
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  blurRadius: number,
): DifferenceStage => {
  const count = validateRgba(pixels, width, height);
  const gray = new Uint8Array(count);
  for (let index = 0, offset = 0; index < count; index += 1, offset += 4) {
    // Integer Rec. 601 weights; deterministic in JS and Wasm.
    gray[index] =
      (77 * pixels[offset] +
        150 * pixels[offset + 1] +
        29 * pixels[offset + 2] +
        128) >>
      8;
  }

  const safeBlurRadius = clamp(
    Math.round(finiteOr(blurRadius, DEFAULT_ROTOSCOPE_OPTIONS.blurRadius)),
    1,
    64,
  );
  const blurred = boxBlur(gray, width, height, safeBlurRadius);
  const difference = new Uint8Array(count);
  for (let index = 0; index < count; index += 1) {
    difference[index] = Math.abs(gray[index] - blurred[index]);
  }
  return { gray, blurred, difference };
};

export const sobelGradient = (
  source: Uint8Array,
  width: number,
  height: number,
): GradientStage => {
  const count = checkedPixelCount(width, height);
  if (source.length !== count) throw new RangeError("source length mismatch");

  const xGradient = new Int16Array(count);
  const yGradient = new Int16Array(count);
  const magnitude = new Uint8Array(count);
  for (let y = 1; y < height - 1; y += 1) {
    const row = y * width;
    for (let x = 1; x < width - 1; x += 1) {
      const index = row + x;
      const topLeft = source[index - width - 1];
      const top = source[index - width];
      const topRight = source[index - width + 1];
      const left = source[index - 1];
      const right = source[index + 1];
      const bottomLeft = source[index + width - 1];
      const bottom = source[index + width];
      const bottomRight = source[index + width + 1];
      const gx =
        -topLeft + topRight - 2 * left + 2 * right - bottomLeft + bottomRight;
      const gy =
        -topLeft - 2 * top - topRight + bottomLeft + 2 * bottom + bottomRight;
      xGradient[index] = gx;
      yGradient[index] = gy;
      magnitude[index] = Math.min(255, (Math.abs(gx) + Math.abs(gy) + 2) >> 2);
    }
  }
  return { x: xGradient, y: yGradient, magnitude };
};

export const minimumEigenvalue = (a: number, b: number, c: number): number => {
  const trace = a + c;
  const discriminant = Math.sqrt((a - c) * (a - c) + 4 * b * b);
  return Math.max(0, (trace - discriminant) * 0.5);
};

export const shiTomasiScores = (
  difference: Uint8Array,
  width: number,
  height: number,
): Float32Array => {
  const gradients = sobelGradient(difference, width, height);
  const count = width * height;
  const scores = new Float32Array(count);

  for (let y = 2; y < height - 2; y += 1) {
    for (let x = 2; x < width - 2; x += 1) {
      let xx = 0;
      let xy = 0;
      let yy = 0;
      for (let dy = -1; dy <= 1; dy += 1) {
        let index = (y + dy) * width + x - 1;
        for (let dx = -1; dx <= 1; dx += 1, index += 1) {
          const gx = gradients.x[index];
          const gy = gradients.y[index];
          xx += gx * gx;
          xy += gx * gy;
          yy += gy * gy;
        }
      }
      scores[y * width + x] = minimumEigenvalue(xx, xy, yy);
    }
  }
  return scores;
};

const pointInPolygon = (
  normalizedX: number,
  normalizedY: number,
  polygon: readonly NormalizedPoint[],
): boolean => {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    const intersects =
      yi > normalizedY !== yj > normalizedY &&
      normalizedX <
        ((xj - xi) * (normalizedY - yi)) / (yj - yi || Number.EPSILON) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
};

export const classifyFocusTier = (
  x: number,
  y: number,
  width: number,
  height: number,
  focus: FocusGeometry,
): FocusTierName => {
  const normalizedX = (x + 0.5) / width;
  const normalizedY = (y + 0.5) / height;
  const faceX =
    (normalizedX - focus.face.centerX) / Math.max(0.0001, focus.face.radiusX);
  const faceY =
    (normalizedY - focus.face.centerY) / Math.max(0.0001, focus.face.radiusY);
  if (faceX * faceX + faceY * faceY <= 1) return "face";
  if (pointInPolygon(normalizedX, normalizedY, focus.body)) return "body";
  return "background";
};

/**
 * Builds the authored priority map once so optimized kernels do not repeat the
 * face ellipse and body polygon test while scanning marker candidates.
 * 0 = face, 1 = body, 2 = background.
 */
export const createFocusTierMap = (
  width: number,
  height: number,
  focus: FocusGeometry,
): Uint8Array => {
  const count = checkedPixelCount(width, height);
  const tiers = new Uint8Array(count);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const tier = classifyFocusTier(x, y, width, height, focus);
      tiers[y * width + x] = tier === "face" ? 0 : tier === "body" ? 1 : 2;
    }
  }
  return tiers;
};

const tierQuota = (
  budget: number,
  quotas: TierValues,
): Record<FocusTierName, number> => {
  const names: readonly FocusTierName[] = ["face", "body", "background"];
  const exact = names.map((name) => budget * quotas[name]);
  const allocated = exact.map(Math.floor);
  let remaining = budget - allocated.reduce((sum, value) => sum + value, 0);
  const remainderOrder = names
    .map((name, index) => ({ index, name, remainder: exact[index] - allocated[index] }))
    .sort((left, right) => right.remainder - left.remainder || left.index - right.index);
  for (let index = 0; index < remainderOrder.length && remaining > 0; index += 1) {
    allocated[remainderOrder[index].index] += 1;
    remaining -= 1;
  }
  return {
    face: allocated[0],
    body: allocated[1],
    background: allocated[2],
  };
};

const isLocalMaximum = (
  scores: Float32Array,
  index: number,
  width: number,
): boolean => {
  const score = scores[index];
  for (let dy = -1; dy <= 1; dy += 1) {
    for (let dx = -1; dx <= 1; dx += 1) {
      if (dx === 0 && dy === 0) continue;
      const neighbor = index + dy * width + dx;
      const neighborScore = scores[neighbor];
      if (neighborScore > score || (neighborScore === score && neighbor < index)) {
        return false;
      }
    }
  }
  return true;
};

const blockDiamond = (
  blocked: Uint8Array,
  index: number,
  width: number,
  height: number,
  radius: number,
): void => {
  const centerY = Math.floor(index / width);
  const centerX = index - centerY * width;
  for (let dy = -radius; dy <= radius; dy += 1) {
    const y = centerY + dy;
    if (y < 0 || y >= height) continue;
    const horizontal = radius - Math.abs(dy);
    for (let dx = -horizontal; dx <= horizontal; dx += 1) {
      const x = centerX + dx;
      if (x >= 0 && x < width) blocked[y * width + x] = 1;
    }
  }
};

const candidatesForTier = (
  tierValue: number,
  scores: Float32Array,
  width: number,
  height: number,
  focusTiers: Uint8Array,
  spacing: number,
): number[] => {
  const count = width * height;
  const seen = new Uint8Array(count);
  const candidates: number[] = [];

  // Strong local maxima preserve the Shi-Tomasi ranking.
  for (let y = 2; y < height - 2; y += 1) {
    for (let x = 2; x < width - 2; x += 1) {
      const index = y * width + x;
      if (
        scores[index] > 0 &&
        focusTiers[index] === tierValue &&
        isLocalMaximum(scores, index, width)
      ) {
        candidates.push(index);
        seen[index] = 1;
      }
    }
  }

  // A best candidate per spatial cell guarantees useful coverage on smooth or
  // low-contrast tiers without allowing a busy background to take the budget.
  const cell = Math.max(2, spacing);
  for (let tileY = 0; tileY < height; tileY += cell) {
    for (let tileX = 0; tileX < width; tileX += cell) {
      let bestIndex = -1;
      let bestScore = -1;
      const yEnd = Math.min(height, tileY + cell);
      const xEnd = Math.min(width, tileX + cell);
      for (let y = tileY; y < yEnd; y += 1) {
        for (let x = tileX; x < xEnd; x += 1) {
          const index = y * width + x;
          if (focusTiers[index] !== tierValue) continue;
          const score = scores[index];
          if (score > bestScore || (score === bestScore && index < bestIndex)) {
            bestIndex = index;
            bestScore = score;
          }
        }
      }
      if (bestIndex >= 0 && seen[bestIndex] === 0) {
        candidates.push(bestIndex);
        seen[bestIndex] = 1;
      }
    }
  }

  candidates.sort((left, right) => scores[right] - scores[left] || left - right);
  return candidates;
};

export const selectMarkers = (
  scores: Float32Array,
  width: number,
  height: number,
  options: Partial<RotoscopeOptions> = {},
): SelectedMarkers => {
  const count = checkedPixelCount(width, height);
  if (scores.length !== count) throw new RangeError("score length mismatch");
  const normalized = normalizeRotoscopeOptions(options, count);
  const quotas = tierQuota(normalized.markerBudget, normalized.quotas);
  const focusTiers = createFocusTierMap(width, height, normalized.focus);
  const blocked = new Uint8Array(count);
  const markers: number[] = [];
  const tierCounts: Record<FocusTierName, number> = {
    face: 0,
    body: 0,
    background: 0,
  };
  const tiers: readonly FocusTierName[] = ["face", "body", "background"];
  const tierValues: Record<FocusTierName, number> = {
    face: 0,
    body: 1,
    background: 2,
  };

  for (const tier of tiers) {
    const spacing = normalized.spacing[tier];
    const candidates = candidatesForTier(
      tierValues[tier],
      scores,
      width,
      height,
      focusTiers,
      spacing,
    );
    for (const index of candidates) {
      if (tierCounts[tier] >= quotas[tier]) break;
      if (blocked[index] !== 0) continue;
      markers.push(index);
      tierCounts[tier] += 1;
      blockDiamond(blocked, index, width, height, spacing);
    }
  }

  // Degenerate images still need one marker so every pixel is labeled.
  if (markers.length === 0) {
    markers.push(Math.floor(count / 2));
    tierCounts.background = 1;
  }
  return { indices: Uint32Array.from(markers), tierCounts };
};

const enqueue = (
  index: number,
  level: number,
  heads: Int32Array,
  tails: Int32Array,
  next: Int32Array,
): void => {
  if (heads[level] < 0) heads[level] = index;
  else next[tails[level]] = index;
  tails[level] = index;
};

/**
 * Deterministic marker-controlled minimum-barrier flood.
 *
 * With an 8-bit source-luminance gradient, the 256 FIFO buckets are a radix
 * priority queue. Each pixel is discovered once in non-decreasing barrier
 * order, which is equivalent to multi-source watershed flooding with stable
 * seed and neighbor tie order.
 */
export const watershed = (
  gradient: Uint8Array,
  width: number,
  height: number,
  markerIndices: Uint32Array,
): WatershedResult => {
  const count = checkedPixelCount(width, height);
  if (gradient.length !== count) throw new RangeError("gradient length mismatch");
  if (markerIndices.length > count) throw new RangeError("too many markers");

  const labels = new Uint32Array(count);
  const queued = new Uint8Array(count);
  const next = new Int32Array(count);
  next.fill(-1);
  const heads = new Int32Array(256);
  const tails = new Int32Array(256);
  heads.fill(-1);
  tails.fill(-1);
  let regionCount = 0;

  for (let marker = 0; marker < markerIndices.length; marker += 1) {
    const index = markerIndices[marker];
    if (index >= count || queued[index] !== 0) continue;
    regionCount += 1;
    labels[index] = regionCount;
    queued[index] = 1;
    enqueue(index, 0, heads, tails, next);
  }
  if (regionCount === 0) {
    const center = Math.floor(count / 2);
    regionCount = 1;
    labels[center] = 1;
    queued[center] = 1;
    enqueue(center, 0, heads, tails, next);
  }

  const neighborX = [-1, 0, 1, -1, 1, -1, 0, 1] as const;
  const neighborY = [-1, -1, -1, 0, 0, 1, 1, 1] as const;
  let level = 0;
  let visited = regionCount;
  while (visited < count) {
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
      const nextLevel = Math.max(level, gradient[nextIndex]);
      enqueue(nextIndex, nextLevel, heads, tails, next);
    }
  }

  return { labels, regionCount };
};

export const colorizeRegions = (
  source: Uint8ClampedArray,
  labels: Uint32Array,
  width: number,
  height: number,
  regionCount: number,
): Uint8ClampedArray => {
  const count = validateRgba(source, width, height);
  if (labels.length !== count) throw new RangeError("label length mismatch");
  if (!Number.isInteger(regionCount) || regionCount <= 0 || regionCount > count) {
    throw new RangeError("invalid region count");
  }
  const red = new Uint32Array(regionCount + 1);
  const green = new Uint32Array(regionCount + 1);
  const blue = new Uint32Array(regionCount + 1);
  const population = new Uint32Array(regionCount + 1);

  for (let index = 0, offset = 0; index < count; index += 1, offset += 4) {
    const label = labels[index];
    if (label === 0 || label > regionCount) continue;
    red[label] += source[offset];
    green[label] += source[offset + 1];
    blue[label] += source[offset + 2];
    population[label] += 1;
  }

  const output = new Uint8ClampedArray(source.length);
  for (let index = 0, offset = 0; index < count; index += 1, offset += 4) {
    const label = labels[index];
    const size = label <= regionCount ? population[label] : 0;
    if (size > 0) {
      output[offset] = Math.round(red[label] / size);
      output[offset + 1] = Math.round(green[label] / size);
      output[offset + 2] = Math.round(blue[label] / size);
    } else {
      output[offset] = source[offset];
      output[offset + 1] = source[offset + 1];
      output[offset + 2] = source[offset + 2];
    }
    output[offset + 3] = source[offset + 3];
  }
  return output;
};

export const runRotoscope = (
  source: Uint8ClampedArray,
  width: number,
  height: number,
  options: Partial<RotoscopeOptions> = {},
): RotoscopeResult => {
  const count = validateRgba(source, width, height);
  const normalized = normalizeRotoscopeOptions(options, count);
  const difference = grayscaleAndDifference(
    source,
    width,
    height,
    normalized.blurRadius,
  );
  const scores = shiTomasiScores(difference.difference, width, height);
  const selected = selectMarkers(scores, width, height, normalized);
  const watershedGradient = sobelGradient(difference.gray, width, height).magnitude;
  const segmented = watershed(
    watershedGradient,
    width,
    height,
    selected.indices,
  );
  return {
    pixels: colorizeRegions(
      source,
      segmented.labels,
      width,
      height,
      segmented.regionCount,
    ),
    markerCount: segmented.regionCount,
    tierCounts: selected.tierCounts,
    mode: "single-image",
  };
};
