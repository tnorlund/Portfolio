import {
  classifyFocusTier,
  colorizeRegions,
  createFocusTierMap,
  grayscaleAndDifference,
  normalizeRotoscopeOptions,
  selectMarkers,
  shiTomasiScores,
  sobelGradient,
  validateRgba,
  watershed,
  type FocusTierName,
  type RotoscopeOptions,
  type TierValues,
} from "../home/Rotoscope/algorithm";
import {
  primaryFaceVisionFeatures,
  type VisionPortraitArtifacts,
} from "./vision";

export type MarkerStrategy = "features" | "radial" | "hybrid" | "vision";
export type NoiseKind = "none" | "white" | "value" | "fbm";

export interface MarkerNoiseOptions {
  kind: NoiseKind;
  seed: number;
  amount: number;
  frequency: number;
  octaves: number;
  lacunarity: number;
  gain: number;
}

export interface RadialDistributionOptions {
  centerX: number;
  centerY: number;
  radiusX: number;
  radiusY: number;
  falloff: number;
  coverage: number;
}

export interface MarkerExperimentOptions {
  strategy: MarkerStrategy;
  radial: RadialDistributionOptions;
  hybridRadialWeight: number;
  noise: MarkerNoiseOptions;
}

export interface MarkerExperimentInput {
  strategy?: MarkerStrategy;
  radial?: Partial<RadialDistributionOptions>;
  hybridRadialWeight?: number;
  noise?: Partial<MarkerNoiseOptions>;
}

export interface PreparedExperimentStages {
  source: Uint8ClampedArray;
  width: number;
  height: number;
  gray: Uint8Array;
  watershedGradient: Uint8Array;
  featureScores?: Float32Array;
}

export interface ExperimentStageTimings {
  noiseMs: number;
  selectionMs: number;
  watershedMs: number;
  colorMs: number;
  diagnosticMs: number;
}

export interface RotoscopeExperimentResult {
  pixels: Uint8ClampedArray;
  diagnosticPixels: Uint8ClampedArray;
  markerIndices: Uint32Array;
  labels: Uint32Array;
  markerCount: number;
  tierCounts: Record<FocusTierName, number>;
  markerDigest: string;
  labelDigest: string;
  visionFeatureCount: number;
  visionMarkerCount: number;
  normalizedBaseOptions: RotoscopeOptions;
  normalizedExperiment: MarkerExperimentOptions;
  timings: ExperimentStageTimings;
}

export const DEFAULT_MARKER_EXPERIMENT: Readonly<MarkerExperimentOptions> = {
  strategy: "radial",
  radial: {
    centerX: 0.4,
    centerY: 0.56,
    radiusX: 0.4,
    radiusY: 0.54,
    falloff: 1.4,
    coverage: 0.18,
  },
  hybridRadialWeight: 0.55,
  noise: {
    kind: "fbm",
    seed: 834821,
    amount: 0.28,
    frequency: 3.2,
    octaves: 4,
    lacunarity: 2,
    gain: 0.5,
  },
};

const clamp = (value: number, low: number, high: number): number =>
  Math.min(high, Math.max(low, value));

const finiteOr = (value: number | undefined, fallback: number): number =>
  value !== undefined && Number.isFinite(value) ? value : fallback;

const isStrategy = (value: unknown): value is MarkerStrategy =>
  value === "features" ||
  value === "radial" ||
  value === "hybrid" ||
  value === "vision";

const isNoiseKind = (value: unknown): value is NoiseKind =>
  value === "none" || value === "white" || value === "value" || value === "fbm";

export const normalizeMarkerExperiment = (
  input: MarkerExperimentInput = {},
): MarkerExperimentOptions => {
  const radial = input.radial ?? {};
  const noise = input.noise ?? {};
  const defaults = DEFAULT_MARKER_EXPERIMENT;
  return {
    strategy: isStrategy(input.strategy) ? input.strategy : defaults.strategy,
    radial: {
      centerX: clamp(finiteOr(radial.centerX, defaults.radial.centerX), -1, 2),
      centerY: clamp(finiteOr(radial.centerY, defaults.radial.centerY), -1, 2),
      radiusX: clamp(finiteOr(radial.radiusX, defaults.radial.radiusX), 0.01, 2),
      radiusY: clamp(finiteOr(radial.radiusY, defaults.radial.radiusY), 0.01, 2),
      falloff: clamp(finiteOr(radial.falloff, defaults.radial.falloff), 0.25, 8),
      coverage: clamp(finiteOr(radial.coverage, defaults.radial.coverage), 0, 0.75),
    },
    hybridRadialWeight: clamp(
      finiteOr(input.hybridRadialWeight, defaults.hybridRadialWeight),
      0,
      1,
    ),
    noise: {
      kind: isNoiseKind(noise.kind) ? noise.kind : defaults.noise.kind,
      seed: Math.trunc(finiteOr(noise.seed, defaults.noise.seed)) >>> 0,
      amount: clamp(finiteOr(noise.amount, defaults.noise.amount), 0, 1),
      frequency: clamp(
        finiteOr(noise.frequency, defaults.noise.frequency),
        1,
        64,
      ),
      octaves: clamp(
        Math.round(finiteOr(noise.octaves, defaults.noise.octaves)),
        1,
        6,
      ),
      lacunarity: clamp(
        finiteOr(noise.lacunarity, defaults.noise.lacunarity),
        1,
        4,
      ),
      gain: clamp(finiteOr(noise.gain, defaults.noise.gain), 0, 1),
    },
  };
};

export const grayscaleForExperiment = (
  source: Uint8ClampedArray,
  width: number,
  height: number,
): Uint8Array => {
  const count = validateRgba(source, width, height);
  const gray = new Uint8Array(count);
  for (let index = 0, offset = 0; index < count; index += 1, offset += 4) {
    gray[index] =
      (77 * source[offset] +
        150 * source[offset + 1] +
        29 * source[offset + 2] +
        128) >>
      8;
  }
  return gray;
};

export const prepareExperimentStages = (
  source: Uint8ClampedArray,
  width: number,
  height: number,
  blurRadius: number,
  includeFeatures: boolean,
): PreparedExperimentStages => {
  const difference = includeFeatures
    ? grayscaleAndDifference(source, width, height, blurRadius)
    : undefined;
  const gray = difference?.gray ?? grayscaleForExperiment(source, width, height);
  let featureScores: Float32Array | undefined;
  if (difference) {
    featureScores = shiTomasiScores(difference.difference, width, height);
  }
  return {
    source,
    width,
    height,
    gray,
    watershedGradient: sobelGradient(gray, width, height).magnitude,
    featureScores,
  };
};

const hash32 = (x: number, y: number, seed: number): number => {
  let hash =
    (seed ^ Math.imul(x | 0, 0x1f123bb5) ^ Math.imul(y | 0, 0x5f356495)) >>>
    0;
  hash = Math.imul(hash ^ (hash >>> 16), 0x45d9f3b) >>> 0;
  hash = Math.imul(hash ^ (hash >>> 16), 0x45d9f3b) >>> 0;
  return (hash ^ (hash >>> 16)) >>> 0;
};

const hashUnit = (x: number, y: number, seed: number): number =>
  Math.fround(hash32(x, y, seed) / 0xffffffff);

const smoothstep = (value: number): number =>
  Math.fround(value * value * (3 - 2 * value));

const interpolate = (start: number, end: number, amount: number): number =>
  Math.fround(start + Math.fround((end - start) * amount));

const valueNoise = (x: number, y: number, seed: number): number => {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const sx = smoothstep(Math.fround(x - x0));
  const sy = smoothstep(Math.fround(y - y0));
  const top = interpolate(hashUnit(x0, y0, seed), hashUnit(x0 + 1, y0, seed), sx);
  const bottom = interpolate(
    hashUnit(x0, y0 + 1, seed),
    hashUnit(x0 + 1, y0 + 1, seed),
    sx,
  );
  return interpolate(top, bottom, sy);
};

const noiseAt = (
  normalizedX: number,
  normalizedY: number,
  options: MarkerNoiseOptions,
  pixelX: number,
  pixelY: number,
): number => {
  if (options.kind === "white") return hashUnit(pixelX, pixelY, options.seed);
  if (options.kind === "value") {
    return valueNoise(
      Math.fround(normalizedX * options.frequency),
      Math.fround(normalizedY * options.frequency),
      options.seed,
    );
  }
  let amplitude = 1;
  let frequency = options.frequency;
  let total = 0;
  let amplitudeTotal = 0;
  for (let octave = 0; octave < options.octaves; octave += 1) {
    const octaveSeed = (options.seed + Math.imul(octave, 0x9e3779b1)) >>> 0;
    total = Math.fround(
      total +
        Math.fround(
          valueNoise(
            Math.fround(normalizedX * frequency),
            Math.fround(normalizedY * frequency),
            octaveSeed,
          ) * amplitude,
        ),
    );
    amplitudeTotal = Math.fround(amplitudeTotal + amplitude);
    frequency = Math.fround(frequency * options.lacunarity);
    amplitude = Math.fround(amplitude * options.gain);
  }
  return amplitudeTotal > 0 ? Math.fround(total / amplitudeTotal) : 0.5;
};

export const createNoiseField = (
  width: number,
  height: number,
  input: MarkerExperimentInput | MarkerExperimentOptions,
): Float32Array | null => {
  const options = normalizeMarkerExperiment(input).noise;
  if (options.kind === "none" || options.amount === 0) return null;
  const field = new Float32Array(width * height);
  const denominatorX = Math.max(1, width - 1);
  const denominatorY = Math.max(1, height - 1);
  for (let y = 0; y < height; y += 1) {
    const normalizedY = Math.fround(y / denominatorY);
    for (let x = 0; x < width; x += 1) {
      field[y * width + x] = noiseAt(
        Math.fround(x / denominatorX),
        normalizedY,
        options,
        x,
        y,
      );
    }
  }
  return field;
};

/** One-entry worker cache key for the generated field, not its later strength. */
export const experimentNoiseCacheKey = (
  width: number,
  height: number,
  input: MarkerExperimentInput | MarkerExperimentOptions,
): string => {
  const { noise } = normalizeMarkerExperiment(input);
  const active = noise.amount > 0 ? 1 : 0;
  if (noise.kind === "none" || active === 0) {
    return JSON.stringify([width, height, noise.kind, active]);
  }
  if (noise.kind === "white") {
    return JSON.stringify([width, height, noise.kind, active, noise.seed]);
  }
  if (noise.kind === "value") {
    return JSON.stringify([
      width,
      height,
      noise.kind,
      active,
      noise.seed,
      noise.frequency,
    ]);
  }
  return JSON.stringify([
    width,
    height,
    noise.kind,
    active,
    noise.seed,
    noise.frequency,
    noise.octaves,
    noise.lacunarity,
    noise.gain,
  ]);
};

const RADIAL_TIER_SPREAD = [1, 1.45, 2.25] as const;

/**
 * Gaussian sampling density with progressively broader body/background tails.
 * The coverage floor keeps every focus tier eligible for deterministic
 * sampling instead of collapsing its markers onto the nearest contour.
 */
export const createRadialDensityField = (
  width: number,
  height: number,
  input: MarkerExperimentInput | MarkerExperimentOptions,
  focusTiers?: Uint8Array,
): Float32Array => {
  const radial = normalizeMarkerExperiment(input).radial;
  const count = width * height;
  if (focusTiers && focusTiers.length !== count) {
    throw new RangeError("focus tier length mismatch");
  }
  const density = new Float32Array(count);
  for (let y = 0; y < height; y += 1) {
    const normalizedY = (y + 0.5) / height;
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x;
      const normalizedX = (x + 0.5) / width;
      const spread = RADIAL_TIER_SPREAD[focusTiers?.[index] ?? 0] ?? 1;
      const dx =
        (normalizedX - radial.centerX) / (radial.radiusX * spread);
      const dy =
        (normalizedY - radial.centerY) / (radial.radiusY * spread);
      const distanceSquared = Math.fround(dx * dx + dy * dy);
      const gaussian = Math.fround(
        Math.exp(Math.fround(-0.5 * radial.falloff * distanceSquared)),
      );
      density[index] = Math.fround(
        radial.coverage + Math.fround((1 - radial.coverage) * gaussian),
      );
    }
  }
  return density;
};

const modulateDensity = (
  base: Float32Array,
  noise: Float32Array | null,
  amount: number,
  coverage: number,
): Float32Array => {
  if (!noise || amount <= 0) return base;
  const output = new Float32Array(base.length);
  for (let index = 0; index < output.length; index += 1) {
    const centeredNoise = Math.fround(noise[index] * 2 - 1);
    const modulation = Math.fround(1 + amount * centeredNoise);
    const aboveFloor = Math.max(0, base[index] - coverage);
    output[index] = Math.fround(
      clamp(coverage + Math.fround(aboveFloor * modulation), coverage, 1),
    );
  }
  return output;
};

/**
 * Converts density into deterministic Gumbel priorities. Ranking these keys is
 * weighted sampling without replacement; the existing spatial suppression
 * then supplies the blue-noise separation.
 */
export const createWeightedPriorityField = (
  density: Float32Array,
  width: number,
  height: number,
  seed: number,
): Float32Array => {
  if (density.length !== width * height) {
    throw new RangeError("density length mismatch");
  }
  const priorities = new Float32Array(density.length);
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;
  const sampleSeed = (seed ^ 0xa511e9b3) >>> 0;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x;
      const uniform = (hash32(x, y, sampleSeed) + 0.5) / 0x100000000;
      const gumbel = -Math.log(-Math.log(uniform));
      const priority = Math.fround(
        Math.log(Math.max(1e-6, density[index])) + gumbel,
      );
      priorities[index] = priority;
      minimum = Math.min(minimum, priority);
      maximum = Math.max(maximum, priority);
    }
  }
  const range = maximum - minimum;
  if (!Number.isFinite(range) || range <= 0) {
    priorities.fill(1);
    return priorities;
  }
  for (let index = 0; index < priorities.length; index += 1) {
    priorities[index] = Math.fround(
      1e-6 + Math.fround(((priorities[index] - minimum) / range) * (1 - 1e-6)),
    );
  }
  return priorities;
};

const normalizeScores = (scores: Float32Array): Float32Array => {
  let maximum = 0;
  for (let index = 0; index < scores.length; index += 1) {
    if (Number.isFinite(scores[index]) && scores[index] > maximum) {
      maximum = scores[index];
    }
  }
  if (maximum <= 0) return new Float32Array(scores.length);
  const normalized = new Float32Array(scores.length);
  for (let index = 0; index < scores.length; index += 1) {
    normalized[index] = Math.fround(Math.max(0, scores[index]) / maximum);
  }
  return normalized;
};

const blendScores = (
  features: Float32Array,
  radial: Float32Array,
  weight: number,
): Float32Array => {
  if (weight <= 0) return features;
  if (weight >= 1) return radial;
  const output = new Float32Array(features.length);
  const featureWeight = Math.fround(1 - weight);
  const radialWeight = Math.fround(weight);
  for (let index = 0; index < output.length; index += 1) {
    output[index] = Math.fround(
      Math.fround(features[index] * featureWeight) +
        Math.fround(radial[index] * radialWeight),
    );
  }
  return output;
};

const perturbScores = (
  base: Float32Array,
  noise: Float32Array | null,
  amount: number,
): Float32Array => {
  if (!noise || amount <= 0) return base;
  const output = new Float32Array(base.length);
  for (let index = 0; index < output.length; index += 1) {
    const offset = Math.fround(amount * Math.fround(noise[index] - 0.5));
    output[index] = Math.fround(clamp(Math.fround(base[index] + offset), 0, 1));
  }
  return output;
};

const visionMaskValue = (
  vision: VisionPortraitArtifacts,
  normalizedX: number,
  normalizedY: number,
): number => {
  const x = Math.min(
    vision.mask.width - 1,
    Math.max(0, Math.floor(normalizedX * vision.mask.width)),
  );
  const y = Math.min(
    vision.mask.height - 1,
    Math.max(0, Math.floor(normalizedY * vision.mask.height)),
  );
  return vision.mask.pixels[y * vision.mask.width + x];
};

/**
 * The selected face landmarks become the strongest exact candidates. The
 * primary-person mask gently biases the remaining Gaussian fill toward Tyler,
 * while pose observations from people in the background remain inspection-only
 * metadata. The seeded priority field keeps non-Vision markers replayable.
 */
export const createVisionPriorityField = (
  width: number,
  height: number,
  experiment: MarkerExperimentOptions,
  baseOptions: RotoscopeOptions,
  noise: Float32Array | null,
  vision?: VisionPortraitArtifacts,
): Float32Array => {
  const focusTiers = createFocusTierMap(width, height, baseOptions.focus);
  let density = createRadialDensityField(
    width,
    height,
    experiment,
    focusTiers,
  );
  density = modulateDensity(
    density,
    noise,
    experiment.noise.amount,
    experiment.radial.coverage,
  );
  if (vision) {
    const personWeighted = new Float32Array(density);
    for (let y = 0; y < height; y += 1) {
      const normalizedY = (y + 0.5) / height;
      for (let x = 0; x < width; x += 1) {
        const normalizedX = (x + 0.5) / width;
        const index = y * width + x;
        if (visionMaskValue(vision, normalizedX, normalizedY)) {
          personWeighted[index] = Math.fround(
            Math.max(personWeighted[index], 0.42 + personWeighted[index] * 0.42),
          );
        }
      }
    }
    density = personWeighted;
  }
  const priorities = createWeightedPriorityField(
    density,
    width,
    height,
    experiment.noise.seed,
  );
  if (!vision) return priorities;

  const primaryFaceFeatures = primaryFaceVisionFeatures(vision);
  for (
    let featureIndex = 0;
    featureIndex < primaryFaceFeatures.length;
    featureIndex += 1
  ) {
    const feature = primaryFaceFeatures[featureIndex];
    const x = Math.min(width - 1, Math.max(0, Math.floor(feature.point.x * width)));
    const y = Math.min(height - 1, Math.max(0, Math.floor(feature.point.y * height)));
    const weight = feature.kind === "face-landmark" ? 4 : 3.8;
    const stableTieBreak = (primaryFaceFeatures.length - featureIndex) * 1e-6;
    priorities[y * width + x] = Math.fround(
      Math.max(
        priorities[y * width + x],
        weight + feature.confidence * 0.2 + stableTieBreak,
      ),
    );
  }
  return priorities;
};

const experimentScores = (
  stages: PreparedExperimentStages,
  baseOptions: RotoscopeOptions,
  experiment: MarkerExperimentOptions,
  noise: Float32Array | null,
  vision?: VisionPortraitArtifacts,
): Float32Array => {
  if (experiment.strategy === "features") {
    if (!stages.featureScores) throw new Error("feature scores were not prepared");
    if (!noise) return stages.featureScores;
    return perturbScores(
      normalizeScores(stages.featureScores),
      noise,
      experiment.noise.amount,
    );
  }

  if (experiment.strategy === "hybrid" && experiment.hybridRadialWeight <= 0) {
    if (!stages.featureScores) throw new Error("feature scores were not prepared");
    if (!noise) return stages.featureScores;
    return perturbScores(
      normalizeScores(stages.featureScores),
      noise,
      experiment.noise.amount,
    );
  }

  if (experiment.strategy === "vision") {
    return createVisionPriorityField(
      stages.width,
      stages.height,
      experiment,
      baseOptions,
      noise,
      vision,
    );
  }

  const focusTiers = createFocusTierMap(
    stages.width,
    stages.height,
    baseOptions.focus,
  );
  const radial = createRadialDensityField(
    stages.width,
    stages.height,
    experiment,
    focusTiers,
  );
  let density = radial;
  if (experiment.strategy === "hybrid" && experiment.hybridRadialWeight < 1) {
    if (!stages.featureScores) throw new Error("feature scores were not prepared");
    density = blendScores(
      normalizeScores(stages.featureScores),
      radial,
      experiment.hybridRadialWeight,
    );
    for (let index = 0; index < density.length; index += 1) {
      density[index] = Math.max(density[index], experiment.radial.coverage);
    }
  }
  density = modulateDensity(
    density,
    noise,
    experiment.noise.amount,
    experiment.radial.coverage,
  );
  return createWeightedPriorityField(
    density,
    stages.width,
    stages.height,
    experiment.noise.seed,
  );
};

const tierForMarker = (
  index: number,
  width: number,
  height: number,
  options: RotoscopeOptions,
): FocusTierName => {
  const y = Math.floor(index / width);
  return classifyFocusTier(index - y * width, y, width, height, options.focus);
};

const diagnosticColor = (tier: FocusTierName, label: number): readonly number[] => {
  const shade = 0.72 + ((hash32(label, label >>> 4, 19) & 255) / 255) * 0.24;
  const base =
    tier === "face"
      ? ([74, 116, 218] as const)
      : tier === "body"
        ? ([224, 139, 65] as const)
        : ([111, 124, 139] as const);
  return base.map((channel) => Math.round(channel * shade));
};

export const createDiagnosticPixels = (
  labels: Uint32Array,
  markerIndices: Uint32Array,
  width: number,
  height: number,
  baseOptions: RotoscopeOptions,
  vision?: VisionPortraitArtifacts,
): Uint8ClampedArray => {
  const regionTier: FocusTierName[] = new Array(markerIndices.length + 1);
  for (let marker = 0; marker < markerIndices.length; marker += 1) {
    regionTier[marker + 1] = tierForMarker(
      markerIndices[marker],
      width,
      height,
      baseOptions,
    );
  }
  const output = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x;
      const label = labels[index];
      const boundary =
        (x > 0 && labels[index - 1] !== label) ||
        (y > 0 && labels[index - width] !== label) ||
        (x + 1 < width && labels[index + 1] !== label) ||
        (y + 1 < height && labels[index + width] !== label);
      const maskValue = vision
        ? visionMaskValue(vision, (x + 0.5) / width, (y + 0.5) / height)
        : 0;
      const personBoundary =
        vision !== undefined &&
        ((x > 0 &&
          visionMaskValue(vision, (x - 0.5) / width, (y + 0.5) / height) !==
            maskValue) ||
          (y > 0 &&
            visionMaskValue(vision, (x + 0.5) / width, (y - 0.5) / height) !==
              maskValue) ||
          (x + 1 < width &&
            visionMaskValue(vision, (x + 1.5) / width, (y + 0.5) / height) !==
              maskValue) ||
          (y + 1 < height &&
            visionMaskValue(vision, (x + 0.5) / width, (y + 1.5) / height) !==
              maskValue));
      const offset = index * 4;
      if (personBoundary) {
        output[offset] = 40;
        output[offset + 1] = 225;
        output[offset + 2] = 205;
      } else if (boundary) {
        output[offset] = 25;
        output[offset + 1] = 29;
        output[offset + 2] = 34;
      } else {
        const [red, green, blue] = diagnosticColor(
          regionTier[label] ?? "background",
          label,
        );
        output[offset] = red;
        output[offset + 1] = green;
        output[offset + 2] = blue;
      }
      output[offset + 3] = 255;
    }
  }
  return output;
};

export const digestUint32 = (values: Uint32Array): string => {
  let hash = 0x811c9dc5;
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    hash = Math.imul(hash ^ (value & 255), 0x01000193) >>> 0;
    hash = Math.imul(hash ^ ((value >>> 8) & 255), 0x01000193) >>> 0;
    hash = Math.imul(hash ^ ((value >>> 16) & 255), 0x01000193) >>> 0;
    hash = Math.imul(hash ^ ((value >>> 24) & 255), 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
};

export const runRotoscopeExperiment = (
  stages: PreparedExperimentStages,
  baseInput: Partial<RotoscopeOptions>,
  experimentInput: MarkerExperimentInput,
  cachedNoise?: Float32Array | null,
  vision?: VisionPortraitArtifacts,
): RotoscopeExperimentResult => {
  const count = validateRgba(
    stages.source,
    stages.width,
    stages.height,
  );
  const normalizedBaseOptions = normalizeRotoscopeOptions(baseInput, count);
  const normalizedExperiment = normalizeMarkerExperiment(experimentInput);
  const noiseStartedAt = performance.now();
  const noise =
    cachedNoise === undefined
      ? createNoiseField(stages.width, stages.height, normalizedExperiment)
      : cachedNoise;
  const noiseMs = performance.now() - noiseStartedAt;

  const selectionStartedAt = performance.now();
  const scores = experimentScores(
    stages,
    normalizedBaseOptions,
    normalizedExperiment,
    noise,
    vision,
  );
  const selected = selectMarkers(
    scores,
    stages.width,
    stages.height,
    normalizedBaseOptions,
  );
  const visionMarkerIndices = new Set<number>();
  const primaryFaceFeatures = vision
    ? primaryFaceVisionFeatures(vision)
    : [];
  if (vision) {
    for (const feature of primaryFaceFeatures) {
      const x = Math.min(
        stages.width - 1,
        Math.max(0, Math.floor(feature.point.x * stages.width)),
      );
      const y = Math.min(
        stages.height - 1,
        Math.max(0, Math.floor(feature.point.y * stages.height)),
      );
      visionMarkerIndices.add(y * stages.width + x);
    }
  }
  let visionMarkerCount = 0;
  for (const index of selected.indices) {
    if (visionMarkerIndices.has(index)) visionMarkerCount += 1;
  }
  const selectionMs = performance.now() - selectionStartedAt;

  const watershedStartedAt = performance.now();
  const segmented = watershed(
    stages.watershedGradient,
    stages.width,
    stages.height,
    selected.indices,
  );
  const watershedMs = performance.now() - watershedStartedAt;

  const colorStartedAt = performance.now();
  const pixels = colorizeRegions(
    stages.source,
    segmented.labels,
    stages.width,
    stages.height,
    segmented.regionCount,
  );
  const colorMs = performance.now() - colorStartedAt;

  const diagnosticStartedAt = performance.now();
  const diagnosticPixels = createDiagnosticPixels(
    segmented.labels,
    selected.indices,
    stages.width,
    stages.height,
    normalizedBaseOptions,
    // The mask still guides density, but keeping its silhouette out of the
    // default diagnostic makes the single selected face unambiguous.
    undefined,
  );
  const diagnosticMs = performance.now() - diagnosticStartedAt;

  return {
    pixels,
    diagnosticPixels,
    markerIndices: selected.indices,
    labels: segmented.labels,
    markerCount: segmented.regionCount,
    tierCounts: selected.tierCounts,
    markerDigest: digestUint32(selected.indices),
    labelDigest: digestUint32(segmented.labels),
    visionFeatureCount: primaryFaceFeatures.length,
    visionMarkerCount,
    normalizedBaseOptions,
    normalizedExperiment,
    timings: { noiseMs, selectionMs, watershedMs, colorMs, diagnosticMs },
  };
};

export const quotaPercentages = (quotas: TierValues): TierValues => {
  const total = quotas.face + quotas.body + quotas.background;
  if (total <= 0) return { face: 0, body: 0, background: 0 };
  return {
    face: (quotas.face / total) * 100,
    body: (quotas.body / total) * 100,
    background: (quotas.background / total) * 100,
  };
};
