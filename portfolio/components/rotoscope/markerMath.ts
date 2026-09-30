import {
  classifyFocusTier,
  createFocusTierMap,
  minimumEigenvalue,
  normalizeRotoscopeOptions,
  selectMarkers,
  shiTomasiScores,
  type FocusTierName,
  type RotoscopeOptions,
} from "../home/Rotoscope/algorithm";
import {
  clampCoord,
  neighborhood,
  neighborhoodNumeric,
  preparePixelFields,
  type PixelFields,
  type RgbaBuffer,
} from "./pixelMath";

export interface MarkerFields extends PixelFields {
  scores: Float32Array;
  focusTiers: Uint8Array;
  markerSet: Set<number>;
  tierCounts: Record<FocusTierName, number>;
  quotaFractions: Record<FocusTierName, number>;
  spacing: Record<FocusTierName, number>;
  markerBudget: number;
}

export const clampInterior = (value: number, size: number): number =>
  Math.min(size - 3, Math.max(2, Math.floor(value)));

export const prepareMarkerFields = (
  source: RgbaBuffer,
  blurRadius: number,
  options: Partial<RotoscopeOptions>,
): MarkerFields => {
  const fields = preparePixelFields(source, blurRadius);
  const scores = shiTomasiScores(fields.difference, fields.width, fields.height);
  const selected = selectMarkers(scores, fields.width, fields.height, options);
  const normalized = normalizeRotoscopeOptions(
    options,
    fields.width * fields.height,
  );
  return {
    ...fields,
    scores,
    focusTiers: createFocusTierMap(fields.width, fields.height, normalized.focus),
    markerSet: new Set(Array.from(selected.indices)),
    tierCounts: selected.tierCounts,
    quotaFractions: normalized.quotas,
    spacing: normalized.spacing,
    markerBudget: normalized.markerBudget,
  };
};

export interface ShiTomasiSample {
  x: number;
  y: number;
  xx: number;
  xy: number;
  yy: number;
  score: number;
  gxWindow: number[][];
  gyWindow: number[][];
  differenceWindow: number[][];
}

export const shiTomasiAt = (
  fields: Pick<MarkerFields, "difference" | "differenceSobel" | "width" | "height">,
  x: number,
  y: number,
): ShiTomasiSample => {
  const cx = clampInterior(x, fields.width);
  const cy = clampInterior(y, fields.height);
  let xx = 0;
  let xy = 0;
  let yy = 0;
  const gxWindow: number[][] = [];
  const gyWindow: number[][] = [];
  for (let dy = -1; dy <= 1; dy += 1) {
    const gxRow: number[] = [];
    const gyRow: number[] = [];
    for (let dx = -1; dx <= 1; dx += 1) {
      const index = (cy + dy) * fields.width + (cx + dx);
      const gx = fields.differenceSobel.x[index];
      const gy = fields.differenceSobel.y[index];
      gxRow.push(gx);
      gyRow.push(gy);
      xx += gx * gx;
      xy += gx * gy;
      yy += gy * gy;
    }
    gxWindow.push(gxRow);
    gyWindow.push(gyRow);
  }
  return {
    x: cx,
    y: cy,
    xx,
    xy,
    yy,
    score: minimumEigenvalue(xx, xy, yy),
    gxWindow,
    gyWindow,
    differenceWindow: neighborhood(
      fields.difference,
      fields.width,
      fields.height,
      cx,
      cy,
      1,
    ),
  };
};

export interface LocalMaxSample {
  x: number;
  y: number;
  score: number;
  kept: boolean;
  window: number[][];
  winner: { dx: number; dy: number } | null;
}

export const localMaxAt = (
  scores: Float32Array,
  width: number,
  height: number,
  x: number,
  y: number,
): LocalMaxSample => {
  const cx = clampInterior(x, width);
  const cy = clampInterior(y, height);
  const index = cy * width + cx;
  const score = scores[index];
  const window = neighborhoodNumeric(scores, width, height, cx, cy, 1);
  let kept = true;
  let winner: { dx: number; dy: number; score: number; index: number } | null =
    null;
  for (let dy = -1; dy <= 1; dy += 1) {
    for (let dx = -1; dx <= 1; dx += 1) {
      if (dx === 0 && dy === 0) continue;
      const nx = cx + dx;
      const ny = cy + dy;
      if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
      const neighbor = ny * width + nx;
      const neighborScore = scores[neighbor];
      if (
        neighborScore > score ||
        (neighborScore === score && neighbor < index)
      ) {
        kept = false;
        if (
          !winner ||
          neighborScore > winner.score ||
          (neighborScore === winner.score && neighbor < winner.index)
        ) {
          winner = { dx, dy, score: neighborScore, index: neighbor };
        }
      }
    }
  }
  return {
    x: cx,
    y: cy,
    score,
    kept,
    window,
    winner: winner ? { dx: winner.dx, dy: winner.dy } : null,
  };
};

export interface MarkerPixel {
  x: number;
  y: number;
  tier: FocusTierName;
  score: number;
  localMax: boolean;
  selected: boolean;
  reason: string;
}

export const markerPixelAt = (
  fields: MarkerFields,
  x: number,
  y: number,
  focus: RotoscopeOptions["focus"],
): MarkerPixel => {
  const cx = clampCoord(Math.floor(x), fields.width - 1);
  const cy = clampCoord(Math.floor(y), fields.height - 1);
  const index = cy * fields.width + cx;
  const local = localMaxAt(fields.scores, fields.width, fields.height, cx, cy);
  const selected = fields.markerSet.has(index);
  const tier = classifyFocusTier(cx, cy, fields.width, fields.height, focus);
  let reason = "Not a local maximum";
  if (selected && local.kept) reason = "Kept as a marker";
  else if (selected) reason = "Kept as a coverage seed, not a strict local max";
  else if (local.kept && local.score > 0) {
    reason = "Local max, skipped (spacing or quota)";
  } else if (local.score === 0) reason = "Score is 0, never a corner candidate";
  return {
    x: cx,
    y: cy,
    tier,
    score: fields.scores[index],
    localMax: local.kept,
    selected,
    reason,
  };
};
