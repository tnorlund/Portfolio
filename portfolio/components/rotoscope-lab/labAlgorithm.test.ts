import {
  grayscaleAndDifference,
  runRotoscope,
  selectMarkers,
  shiTomasiScores,
  type RotoscopeOptions,
} from "../home/Rotoscope/algorithm";
import {
  createDiagnosticPixels,
  experimentNoiseCacheKey,
  createNoiseField,
  createRadialDensityField,
  createWeightedPriorityField,
  normalizeMarkerExperiment,
  prepareExperimentStages,
  runRotoscopeExperiment,
} from "./labAlgorithm";

const rgba = (width: number, height: number): Uint8ClampedArray => {
  const source = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = (y * width + x) * 4;
      source[offset] = (x * 31 + y * 7) % 256;
      source[offset + 1] = (x * 11 + y * 37) % 256;
      source[offset + 2] = (x * 17 + y * 13) % 256;
      source[offset + 3] = (x + y) % 2 === 0 ? 255 : 190;
    }
  }
  return source;
};

const allFaceOptions: Partial<RotoscopeOptions> = {
  blurRadius: 2,
  markerBudget: 7,
  quotas: { face: 1, body: 0, background: 0 },
  spacing: { face: 1, body: 1, background: 1 },
  focus: {
    face: { centerX: 0.5, centerY: 0.5, radiusX: 2, radiusY: 2 },
    body: [],
  },
};

test("normalizes every experimental value into a bounded deterministic contract", () => {
  const normalized = normalizeMarkerExperiment({
    strategy: "radial",
    radial: {
      centerX: Number.NaN,
      centerY: 99,
      radiusX: 0,
      radiusY: Number.POSITIVE_INFINITY,
      falloff: 99,
      coverage: -1,
    },
    hybridRadialWeight: -5,
    noise: {
      kind: "fbm",
      seed: -1,
      amount: 99,
      frequency: 999,
      octaves: 99,
      lacunarity: 0,
      gain: -1,
    },
  });
  expect(normalized.radial).toEqual({
    centerX: 0.4,
    centerY: 2,
    radiusX: 0.01,
    radiusY: 0.54,
    falloff: 8,
    coverage: 0,
  });
  expect(normalized.hybridRadialWeight).toBe(0);
  expect(normalized.noise).toEqual({
    kind: "fbm",
    seed: 0xffffffff,
    amount: 1,
    frequency: 64,
    octaves: 6,
    lacunarity: 1,
    gain: 0,
  });
});

test("white, value, and fractal noise match exact seeded goldens", () => {
  const options = {
    noise: {
      seed: 123,
      amount: 0.5,
      frequency: 2.5,
      octaves: 3,
      lacunarity: 2,
      gain: 0.5,
    },
  };
  const samples = (kind: "white" | "value" | "fbm") =>
    Array.from(createNoiseField(4, 3, { ...options, noise: { ...options.noise, kind } }) ?? []).map(
      (value) => Number(value.toFixed(8)),
    );
  expect(samples("white")).toEqual([
    0.36819983, 0.07333979, 0.77787572, 0.45431429,
    0.36325133, 0.96456522, 0.05309421, 0.98645067,
    0.66577053, 0.90241939, 0.87301052, 0.44910324,
  ]);
  expect(samples("value")).toEqual([
    0.36819983, 0.09518126, 0.5952183, 0.61609501,
    0.41051996, 0.91453385, 0.38178167, 0.54184818,
    0.62288761, 0.65509957, 0.78667885, 0.54712713,
  ]);
  expect(samples("fbm")).toEqual([
    0.51293302, 0.22128025, 0.54695696, 0.59853923,
    0.43686709, 0.67820865, 0.44096893, 0.48633656,
    0.62575674, 0.57410973, 0.80770361, 0.39795703,
  ]);
  expect(createNoiseField(4, 3, { noise: { kind: "none", seed: 1 } })).toBeNull();
  expect(createNoiseField(4, 3, { noise: { kind: "white", amount: 0 } })).toBeNull();
});

test("noise cache keys distinguish disabled fields and reuse positive-strength fields", () => {
  const base = {
    noise: {
      kind: "fbm" as const,
      seed: 42,
      amount: 0.2,
      frequency: 3,
      octaves: 4,
      lacunarity: 2,
      gain: 0.5,
    },
  };
  const positive = experimentNoiseCacheKey(40, 30, base);
  expect(
    experimentNoiseCacheKey(40, 30, {
      noise: { ...base.noise, amount: 0.9 },
    }),
  ).toBe(positive);
  expect(
    experimentNoiseCacheKey(40, 30, {
      noise: { ...base.noise, amount: 0 },
    }),
  ).not.toBe(positive);

  const white = {
    noise: { ...base.noise, kind: "white" as const },
  };
  expect(
    experimentNoiseCacheKey(40, 30, {
      noise: { ...white.noise, frequency: 63, octaves: 1, gain: 0.1 },
    }),
  ).toBe(experimentNoiseCacheKey(40, 30, white));
});

test("the radial field is a Gaussian density with an explicit coverage floor", () => {
  const density = createRadialDensityField(3, 3, {
    radial: {
      centerX: 0.5,
      centerY: 0.5,
      radiusX: 0.5,
      radiusY: 0.5,
      falloff: 1,
      coverage: 0,
    },
  });
  expect(Array.from(density).map((value) => Number(value.toFixed(8)))).toEqual([
    0.6411804, 0.80073738, 0.6411804,
    0.80073738, 1, 0.80073738,
    0.6411804, 0.80073738, 0.6411804,
  ]);

  const tailed = createRadialDensityField(3, 3, {
    radial: { coverage: 0.25 },
  });
  expect(Math.min(...tailed)).toBeGreaterThanOrEqual(0.25);

  const faceTail = createRadialDensityField(
    3,
    3,
    { radial: { coverage: 0 } },
    new Uint8Array(9).fill(0),
  );
  const bodyTail = createRadialDensityField(
    3,
    3,
    { radial: { coverage: 0 } },
    new Uint8Array(9).fill(1),
  );
  const backgroundTail = createRadialDensityField(
    3,
    3,
    { radial: { coverage: 0 } },
    new Uint8Array(9).fill(2),
  );
  expect(faceTail[0]).toBeLessThan(bodyTail[0]);
  expect(bodyTail[0]).toBeLessThan(backgroundTail[0]);
});

test("weighted priorities are seeded, finite, and preserve nonzero eligibility", () => {
  const density = new Float32Array([1, 0.75, 0.5, 0.25, 0.1, 0.01]);
  const first = createWeightedPriorityField(density, 3, 2, 123);
  const replay = createWeightedPriorityField(density, 3, 2, 123);
  const changed = createWeightedPriorityField(density, 3, 2, 124);
  expect(Array.from(replay)).toEqual(Array.from(first));
  expect(Array.from(changed)).not.toEqual(Array.from(first));
  expect(Array.from(first).every((value) => Number.isFinite(value) && value > 0)).toBe(true);
});

test("coverage produces a deterministic distributed tail with blue-noise spacing", () => {
  const width = 80;
  const height = 60;
  const source = rgba(width, height);
  const stages = prepareExperimentStages(source, width, height, 2, false);
  const base = {
    ...allFaceOptions,
    markerBudget: 160,
    spacing: { face: 2, body: 2, background: 2 },
  };
  const render = (coverage: number) =>
    runRotoscopeExperiment(stages, base, {
      strategy: "radial",
      radial: {
        centerX: 0.5,
        centerY: 0.5,
        radiusX: 0.18,
        radiusY: 0.18,
        falloff: 4,
        coverage,
      },
      noise: { kind: "none", amount: 0, seed: 834821 },
    });
  const concentrated = render(0);
  const distributed = render(0.18);
  const outsideTwoSigma = (indices: Uint32Array) =>
    Array.from(indices).filter((index) => {
      const y = Math.floor(index / width);
      const x = index - y * width;
      const dx = ((x + 0.5) / width - 0.5) / 0.18;
      const dy = ((y + 0.5) / height - 0.5) / 0.18;
      return Math.hypot(dx, dy) > 2;
    }).length;

  expect(outsideTwoSigma(distributed.markerIndices)).toBeGreaterThan(
    outsideTwoSigma(concentrated.markerIndices) + 60,
  );
  expect(outsideTwoSigma(distributed.markerIndices)).toBeGreaterThan(80);
  for (let left = 0; left < distributed.markerIndices.length; left += 1) {
    const leftIndex = distributed.markerIndices[left];
    const leftY = Math.floor(leftIndex / width);
    const leftX = leftIndex - leftY * width;
    for (let right = left + 1; right < distributed.markerIndices.length; right += 1) {
      const rightIndex = distributed.markerIndices[right];
      const rightY = Math.floor(rightIndex / width);
      const rightX = rightIndex - rightY * width;
      expect(Math.abs(leftX - rightX) + Math.abs(leftY - rightY)).toBeGreaterThan(2);
    }
  }
});

test("feature plus no noise is pixel-for-pixel identical to the production scalar", () => {
  const width = 17;
  const height = 13;
  const source = rgba(width, height);
  const stages = prepareExperimentStages(source, width, height, 2, true);
  const lab = runRotoscopeExperiment(stages, allFaceOptions, {
    strategy: "features",
    noise: { kind: "none", amount: 0 },
  });
  const production = runRotoscope(source, width, height, allFaceOptions);
  expect(Array.from(lab.pixels)).toEqual(Array.from(production.pixels));
  expect(lab.markerCount).toBe(production.markerCount);
  expect(lab.tierCounts).toEqual(production.tierCounts);
  const difference = grayscaleAndDifference(source, width, height, 2).difference;
  const expectedMarkers = selectMarkers(
    shiTomasiScores(difference, width, height),
    width,
    height,
    lab.normalizedBaseOptions,
  );
  expect(Array.from(lab.markerIndices)).toEqual(Array.from(expectedMarkers.indices));
});

test("radial markers and hybrid endpoints are exact and deterministic", () => {
  const width = 9;
  const height = 7;
  const source = rgba(width, height);
  const radialStages = prepareExperimentStages(source, width, height, 2, false);
  const featureStages = prepareExperimentStages(source, width, height, 2, true);
  const radial = runRotoscopeExperiment(radialStages, allFaceOptions, {
    strategy: "radial",
    noise: { kind: "none", amount: 0 },
  });
  const feature = runRotoscopeExperiment(featureStages, allFaceOptions, {
    strategy: "features",
    noise: { kind: "none", amount: 0 },
  });
  const featureEndpoint = runRotoscopeExperiment(featureStages, allFaceOptions, {
    strategy: "hybrid",
    hybridRadialWeight: 0,
    noise: { kind: "none", amount: 0 },
  });
  const radialEndpoint = runRotoscopeExperiment(radialStages, allFaceOptions, {
    strategy: "hybrid",
    hybridRadialWeight: 1,
    noise: { kind: "none", amount: 0 },
  });

  expect(Array.from(radial.markerIndices)).toEqual([53, 31, 46, 39, 49, 13, 21]);
  expect(radial.markerDigest).toBe("ae7323cf");
  expect(Array.from(featureEndpoint.markerIndices)).toEqual(
    Array.from(feature.markerIndices),
  );
  expect(Array.from(radialEndpoint.markerIndices)).toEqual(
    Array.from(radial.markerIndices),
  );
  expect(new Set(radial.markerIndices).size).toBe(radial.markerIndices.length);
  expect(Array.from(radial.labels).every((label) => label > 0)).toBe(true);
});

test("strategy and noise never alter grayscale or the watershed gradient", () => {
  const source = rgba(21, 15);
  const radialStages = prepareExperimentStages(source, 21, 15, 4, false);
  const featureStages = prepareExperimentStages(source, 21, 15, 4, true);
  expect(Array.from(radialStages.gray)).toEqual(Array.from(featureStages.gray));
  expect(Array.from(radialStages.watershedGradient)).toEqual(
    Array.from(featureStages.watershedGradient),
  );
});

test("same seeded settings replay exactly while different seeds change markers", () => {
  const width = 40;
  const height = 30;
  const source = rgba(width, height);
  const stages = prepareExperimentStages(source, width, height, 3, false);
  const base = {
    ...allFaceOptions,
    markerBudget: 40,
    spacing: { face: 1, body: 1, background: 1 },
  };
  const experiment = {
    strategy: "radial" as const,
    noise: { kind: "white" as const, seed: 42, amount: 0.8 },
  };
  const first = runRotoscopeExperiment(stages, base, experiment);
  const replay = runRotoscopeExperiment(stages, base, experiment);
  const changed = runRotoscopeExperiment(stages, base, {
    ...experiment,
    noise: { ...experiment.noise, seed: 43 },
  });
  expect(replay.markerDigest).toBe(first.markerDigest);
  expect(replay.labelDigest).toBe(first.labelDigest);
  expect(Array.from(replay.pixels)).toEqual(Array.from(first.pixels));
  expect(changed.markerDigest).not.toBe(first.markerDigest);
});

test("diagnostic colors encode tiers and darken exact basin boundaries", () => {
  const labels = new Uint32Array([1, 1, 1, 2, 2, 2, 3, 3, 3]);
  const markers = new Uint32Array([1, 4, 7]);
  const options: RotoscopeOptions = {
    blurRadius: 1,
    markerBudget: 3,
    quotas: { face: 1 / 3, body: 1 / 3, background: 1 / 3 },
    spacing: { face: 1, body: 1, background: 1 },
    focus: {
      face: {
        centerX: 1.5 / 9,
        centerY: 0.5,
        radiusX: 0.16,
        radiusY: 0.6,
      },
      body: [
        [0.35, 0],
        [0.65, 0],
        [0.65, 1],
        [0.35, 1],
      ],
    },
  };
  expect(Array.from(createDiagnosticPixels(labels, markers, 9, 1, options))).toEqual([
    60, 94, 177, 255, 60, 94, 177, 255, 25, 29, 34, 255,
    25, 29, 34, 255, 169, 105, 49, 255, 25, 29, 34, 255,
    25, 29, 34, 255, 89, 99, 111, 255, 89, 99, 111, 255,
  ]);
});

test("diagnostic output is opaque and does not affect the rendered pixels", () => {
  const source = rgba(9, 7);
  const stages = prepareExperimentStages(source, 9, 7, 2, false);
  const result = runRotoscopeExperiment(stages, allFaceOptions, {
    strategy: "radial",
    noise: { kind: "none" },
  });
  expect(result.diagnosticPixels).toHaveLength(source.length);
  for (let alpha = 3; alpha < result.diagnosticPixels.length; alpha += 4) {
    expect(result.diagnosticPixels[alpha]).toBe(255);
  }
  for (let alpha = 3; alpha < result.pixels.length; alpha += 4) {
    expect(result.pixels[alpha]).toBe(source[alpha]);
  }
});
