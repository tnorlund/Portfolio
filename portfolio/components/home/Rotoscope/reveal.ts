import {
  classifyFocusTier,
  type FocusGeometry,
  type FocusTierName,
} from "./algorithm";

export const BASIN_REVEAL_PHASE_COUNT = 36;
export type BasinRevealAct = "features" | "subject" | "background";

export const basinRevealActForPhase = (
  phase: number,
  phaseCount: number,
): BasinRevealAct => {
  const lastPhase = Math.max(1, phaseCount - 1);
  if (phase < Math.floor(0.34 * lastPhase)) return "features";
  if (phase < Math.floor(0.68 * lastPhase)) return "subject";
  return "background";
};

export interface BasinRevealMap {
  phases: Uint8Array;
  basinCount: number;
  phaseCount: number;
}

export interface BasinRevealFeatureRegion {
  centerX: number;
  centerY: number;
  radiusX: number;
  radiusY: number;
  order: number;
}

export interface BasinRevealPersonMask {
  width: number;
  height: number;
  pixels: Uint8Array;
}

export interface BasinRevealSemantics {
  primaryFeatures: readonly BasinRevealFeatureRegion[];
  personMask?: BasinRevealPersonMask;
}

const TIER_SCHEDULE: Record<
  FocusTierName,
  { start: number; stagger: number; radialSpan: number }
> = {
  face: { start: 0, stagger: 0.14, radialSpan: 0.27 },
  body: { start: 0.25, stagger: 0.18, radialSpan: 0.28 },
  background: { start: 0.55, stagger: 0.17, radialSpan: 0.28 },
};

const SEMANTIC_SCHEDULE = {
  primary: { start: 0, stagger: 0.04, radialSpan: 0.25 },
  subject: { start: 0.34, stagger: 0.06, radialSpan: 0.24 },
  background: { start: 0.68, stagger: 0.06, radialSpan: 0.25 },
} as const;

const validate = (
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  phaseCount: number,
): number => {
  const count = width * height;
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width <= 0 ||
    height <= 0 ||
    !Number.isSafeInteger(count) ||
    pixels.length !== count * 4 ||
    !Number.isInteger(phaseCount) ||
    phaseCount < 2 ||
    phaseCount > 255
  ) {
    throw new Error("invalid basin reveal input");
  }
  return count;
};

const sameColor = (
  pixels: Uint8ClampedArray,
  left: number,
  right: number,
): boolean => {
  const leftOffset = left * 4;
  const rightOffset = right * 4;
  return (
    pixels[leftOffset] === pixels[rightOffset] &&
    pixels[leftOffset + 1] === pixels[rightOffset + 1] &&
    pixels[leftOffset + 2] === pixels[rightOffset + 2]
  );
};

/**
 * Treat each connected flat-color region in the rotoscoped result as a basin.
 * Regions are staged face → body → background, then every pixel receives a
 * local radial threshold measured from a stable point inside its own basin.
 */
export const createBasinRevealMap = (
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  focus: FocusGeometry,
  phaseCount = BASIN_REVEAL_PHASE_COUNT,
  semantics?: BasinRevealSemantics,
): BasinRevealMap => {
  const count = validate(pixels, width, height, phaseCount);
  const labels = new Uint32Array(count);
  const queue = new Uint32Array(count);
  const sumsX = [0];
  const sumsY = [0];
  const sizes = [0];
  let basinCount = 0;

  for (let first = 0; first < count; first += 1) {
    if (labels[first] !== 0) continue;
    basinCount += 1;
    let head = 0;
    let tail = 0;
    let sumX = 0;
    let sumY = 0;
    let size = 0;
    labels[first] = basinCount;
    queue[tail] = first;
    tail += 1;

    while (head < tail) {
      const index = queue[head];
      head += 1;
      const y = Math.floor(index / width);
      const x = index - y * width;
      sumX += x;
      sumY += y;
      size += 1;

      if (x > 0) {
        const next = index - 1;
        if (labels[next] === 0 && sameColor(pixels, first, next)) {
          labels[next] = basinCount;
          queue[tail] = next;
          tail += 1;
        }
      }
      if (x + 1 < width) {
        const next = index + 1;
        if (labels[next] === 0 && sameColor(pixels, first, next)) {
          labels[next] = basinCount;
          queue[tail] = next;
          tail += 1;
        }
      }
      if (y > 0) {
        const next = index - width;
        if (labels[next] === 0 && sameColor(pixels, first, next)) {
          labels[next] = basinCount;
          queue[tail] = next;
          tail += 1;
        }
      }
      if (y + 1 < height) {
        const next = index + width;
        if (labels[next] === 0 && sameColor(pixels, first, next)) {
          labels[next] = basinCount;
          queue[tail] = next;
          tail += 1;
        }
      }
    }

    sumsX[basinCount] = sumX;
    sumsY[basinCount] = sumY;
    sizes[basinCount] = size;
  }

  const centerX = new Float64Array(basinCount + 1);
  const centerY = new Float64Array(basinCount + 1);
  const seeds = new Uint32Array(basinCount + 1);
  const seedDistance = new Float64Array(basinCount + 1);
  seedDistance.fill(Number.POSITIVE_INFINITY);
  for (let label = 1; label <= basinCount; label += 1) {
    centerX[label] = sumsX[label] / sizes[label];
    centerY[label] = sumsY[label] / sizes[label];
  }

  // A geometric centroid can fall outside a concave basin. Choose the basin
  // pixel nearest that centroid so every local reveal begins inside its region.
  for (let index = 0; index < count; index += 1) {
    const label = labels[index];
    const y = Math.floor(index / width);
    const x = index - y * width;
    const dx = x - centerX[label];
    const dy = y - centerY[label];
    const distance = dx * dx + dy * dy;
    if (
      distance < seedDistance[label] ||
      (distance === seedDistance[label] && index < seeds[label])
    ) {
      seedDistance[label] = distance;
      seeds[label] = index;
    }
  }

  const maximumRadiusSquared = new Float64Array(basinCount + 1);
  for (let index = 0; index < count; index += 1) {
    const label = labels[index];
    const seed = seeds[label];
    const y = Math.floor(index / width);
    const x = index - y * width;
    const seedY = Math.floor(seed / width);
    const seedX = seed - seedY * width;
    const dx = x - seedX;
    const dy = y - seedY;
    maximumRadiusSquared[label] = Math.max(
      maximumRadiusSquared[label],
      dx * dx + dy * dy,
    );
  }

  const tiers: Record<FocusTierName, number[]> = {
    face: [],
    body: [],
    background: [],
  };
  const focusTierForLabel = new Uint8Array(basinCount + 1);
  const distanceFromFace = new Float64Array(basinCount + 1);
  for (let label = 1; label <= basinCount; label += 1) {
    const seed = seeds[label];
    const seedY = Math.floor(seed / width);
    const seedX = seed - seedY * width;
    const tier = classifyFocusTier(seedX, seedY, width, height, focus);
    tiers[tier].push(label);
    focusTierForLabel[label] = tier === "face" ? 0 : tier === "body" ? 1 : 2;
    const normalizedX = (seedX + 0.5) / width;
    const normalizedY = (seedY + 0.5) / height;
    const dx =
      (normalizedX - focus.face.centerX) / Math.max(focus.face.radiusX, 1e-6);
    const dy =
      (normalizedY - focus.face.centerY) / Math.max(focus.face.radiusY, 1e-6);
    distanceFromFace[label] = dx * dx + dy * dy;
  }

  const start = new Float64Array(basinCount + 1);
  const radialSpan = new Float64Array(basinCount + 1);
  if (semantics) {
    const personMask = semantics.personMask;
    if (
      personMask &&
      (!Number.isInteger(personMask.width) ||
        !Number.isInteger(personMask.height) ||
        personMask.width <= 0 ||
        personMask.height <= 0 ||
        personMask.pixels.length !== personMask.width * personMask.height)
    ) {
      throw new Error("invalid basin reveal person mask");
    }

    const primaryOrder = new Float64Array(basinCount + 1);
    const primaryDistance = new Float64Array(basinCount + 1);
    const personMatch = new Uint8Array(basinCount + 1);
    primaryOrder.fill(Number.POSITIVE_INFINITY);
    primaryDistance.fill(Number.POSITIVE_INFINITY);
    const maskValue = (x: number, y: number): number => {
      if (!personMask) return 0;
      const maskX = Math.min(
        personMask.width - 1,
        Math.max(0, Math.floor(x * personMask.width)),
      );
      const maskY = Math.min(
        personMask.height - 1,
        Math.max(0, Math.floor(y * personMask.height)),
      );
      return personMask.pixels[maskY * personMask.width + maskX];
    };

    // Basin seeds and centroids are already stable interior representatives.
    // Sampling those avoids rescanning the entire image for semantic staging.
    for (let label = 1; label <= basinCount; label += 1) {
      const seed = seeds[label];
      const seedY = Math.floor(seed / width);
      const seedX = seed - seedY * width;
      const normalizedX = (seedX + 0.5) / width;
      const normalizedY = (seedY + 0.5) / height;
      if (personMask) {
        personMatch[label] =
          maskValue(normalizedX, normalizedY) === 1 ||
          maskValue(
            (centerX[label] + 0.5) / width,
            (centerY[label] + 0.5) / height,
          ) === 1
            ? 1
            : 0;
      }
      for (const feature of semantics.primaryFeatures) {
        const dx =
          (normalizedX - feature.centerX) / Math.max(feature.radiusX, 1e-6);
        const dy =
          (normalizedY - feature.centerY) / Math.max(feature.radiusY, 1e-6);
        const distance = dx * dx + dy * dy;
        if (
          distance <= 1 &&
          (feature.order < primaryOrder[label] ||
            (feature.order === primaryOrder[label] &&
              distance < primaryDistance[label]))
        ) {
          primaryOrder[label] = feature.order;
          primaryDistance[label] = distance;
        }
      }
    }

    // Always include the basin under each exact Vision region center, even if
    // that basin's own interior seed falls just outside the compact ellipse.
    for (const feature of semantics.primaryFeatures) {
      const x = Math.min(
        width - 1,
        Math.max(0, Math.floor(feature.centerX * width)),
      );
      const y = Math.min(
        height - 1,
        Math.max(0, Math.floor(feature.centerY * height)),
      );
      const label = labels[y * width + x];
      if (feature.order < primaryOrder[label]) {
        primaryOrder[label] = feature.order;
        primaryDistance[label] = 0;
      }
    }

    const acts: Record<keyof typeof SEMANTIC_SCHEDULE, number[]> = {
      primary: [],
      subject: [],
      background: [],
    };
    for (let label = 1; label <= basinCount; label += 1) {
      if (Number.isFinite(primaryOrder[label])) {
        acts.primary.push(label);
        continue;
      }
      const subject = personMask
        ? personMatch[label] === 1 || focusTierForLabel[label] !== 2
        : focusTierForLabel[label] !== 2;
      acts[subject ? "subject" : "background"].push(label);
    }

    acts.primary.sort(
      (left, right) =>
        primaryOrder[left] - primaryOrder[right] ||
        primaryDistance[left] - primaryDistance[right] ||
        left - right,
    );
    acts.subject.sort(
      (left, right) =>
        distanceFromFace[left] - distanceFromFace[right] || left - right,
    );
    acts.background.sort(
      (left, right) =>
        distanceFromFace[left] - distanceFromFace[right] || left - right,
    );

    for (const act of ["primary", "subject", "background"] as const) {
      const labelsInAct = acts[act];
      const schedule = SEMANTIC_SCHEDULE[act];
      for (let rank = 0; rank < labelsInAct.length; rank += 1) {
        const label = labelsInAct[rank];
        const progress =
          labelsInAct.length <= 1 ? 0 : rank / (labelsInAct.length - 1);
        start[label] = schedule.start + schedule.stagger * progress;
        radialSpan[label] = schedule.radialSpan;
      }
    }
  } else {
    for (const tier of ["face", "body", "background"] as const) {
      const labelsInTier = tiers[tier];
      labelsInTier.sort(
        (left, right) =>
          distanceFromFace[left] - distanceFromFace[right] || left - right,
      );
      const schedule = TIER_SCHEDULE[tier];
      for (let rank = 0; rank < labelsInTier.length; rank += 1) {
        const label = labelsInTier[rank];
        const progress =
          labelsInTier.length <= 1 ? 0 : rank / (labelsInTier.length - 1);
        start[label] = schedule.start + schedule.stagger * progress;
        radialSpan[label] = schedule.radialSpan;
      }
    }
  }

  const phases = new Uint8Array(count);
  for (let index = 0; index < count; index += 1) {
    const label = labels[index];
    const seed = seeds[label];
    const y = Math.floor(index / width);
    const x = index - y * width;
    const seedY = Math.floor(seed / width);
    const seedX = seed - seedY * width;
    const dx = x - seedX;
    const dy = y - seedY;
    const maximum = maximumRadiusSquared[label];
    const localProgress =
      maximum <= 0 ? 0 : Math.sqrt((dx * dx + dy * dy) / maximum);
    const threshold = Math.min(
      1,
      start[label] + radialSpan[label] * localProgress,
    );
    phases[index] = Math.min(
      phaseCount - 1,
      Math.floor(threshold * (phaseCount - 1)),
    );
  }

  return { phases, basinCount, phaseCount };
};

/** Copy only newly activated basin pixels into one reusable transparent frame. */
export const applyBasinRevealPhase = (
  target: Uint8ClampedArray,
  source: Uint8ClampedArray,
  phases: Uint8Array,
  previousPhase: number,
  nextPhase: number,
): void => {
  if (
    target.length !== source.length ||
    phases.length * 4 !== source.length ||
    nextPhase < previousPhase
  ) {
    throw new Error("invalid basin reveal frame");
  }
  for (let index = 0; index < phases.length; index += 1) {
    const phase = phases[index];
    if (phase <= previousPhase || phase > nextPhase) continue;
    const offset = index * 4;
    target[offset] = source[offset];
    target[offset + 1] = source[offset + 1];
    target[offset + 2] = source[offset + 2];
    target[offset + 3] = source[offset + 3];
  }
};
