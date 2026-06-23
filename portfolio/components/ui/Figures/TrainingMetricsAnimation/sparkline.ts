/**
 * Pure helpers for the EpochSparkline component. Extracted so the math
 * (scales, click-to-index, axis-label collision) is unit-testable without
 * rendering the SVG.
 *
 * SVG layout convention used here:
 *   viewBox 0 0 W H (W = svg viewBox width, H = svg viewBox height)
 *   plot area: x in [padX, W - padX], y in [padTop, H - padBottom]
 */

import type { TrainingMetricsEpoch } from "../../../../types/api";

export type TrainingMetricKey = keyof TrainingMetricsEpoch["metrics"];

export interface SparklineDims {
  /** SVG viewBox width in user units. */
  width: number;
  /** SVG viewBox height in user units. */
  height: number;
  /** Inner padding on the left/right of the plot area. */
  padX: number;
  /** Inner padding on top (room for BEST label). */
  padTop: number;
  /** Inner padding at bottom (room for x-axis labels). */
  padBottom: number;
}

export interface SparklineScales {
  /** SVG x-coord for an epoch index in [0, n-1]. */
  xScale: (i: number) => number;
  /** SVG y-coord for a val_f1 value in [0, 1]. */
  yScale: (v: number) => number;
  /** SVG "points" string for the convergence polyline (empty if n < 1). */
  points: string;
  /** Data-derived y range actually used after the min-span floor. */
  dataMin: number;
  dataMax: number;
}

/**
 * Minimum vertical span of the y-axis. A run that plateaus at f1≈0.79 with
 * 0.01 noise would otherwise be plotted across a 0.05 span, amplifying noise
 * into a dramatic curve. Floor at 0.1 so small variation looks small.
 */
const MIN_Y_SPAN = 0.1;

/**
 * Build the scale functions and curve polyline for a set of epochs.
 *
 * Edge cases:
 *  - epochs.length === 0: scales return the left padding for any input,
 *    points is "".
 *  - epochs.length === 1: xScale always returns the plot midpoint.
 *  - all val_f1 identical: y range expands to MIN_Y_SPAN centered on the
 *    value (clipped to [0, 1]) so a flat run still renders cleanly.
 */
export function computeScales(
  epochs: TrainingMetricsEpoch[],
  dims: SparklineDims,
  metricKeys: TrainingMetricKey[] = ["val_f1"]
): SparklineScales {
  const { width, height, padX, padTop, padBottom } = dims;
  const plotLeft = padX;
  const plotRight = width - padX;
  const plotTop = padTop;
  const plotBottom = height - padBottom;
  const plotWidth = plotRight - plotLeft;
  const plotHeight = plotBottom - plotTop;

  if (epochs.length === 0) {
    return {
      xScale: () => plotLeft,
      yScale: () => plotTop,
      points: "",
      dataMin: 0,
      dataMax: 1,
    };
  }

  const values = epochs.flatMap((e) =>
    metricKeys
      .map((key) => e.metrics?.[key])
      .filter((value): value is number => Number.isFinite(value))
  );
  const f1Values = epochs.map((e) => e.metrics?.val_f1 ?? 0);
  const scaleValues = values.length > 0 ? values : f1Values;
  let dataMin = Math.max(0, Math.min(...scaleValues) - 0.05);
  let dataMax = Math.min(1, Math.max(...scaleValues) + 0.05);

  // Enforce minimum visible span so converged-run noise doesn't look dramatic.
  if (dataMax - dataMin < MIN_Y_SPAN) {
    const mid = (dataMin + dataMax) / 2;
    dataMin = Math.max(0, mid - MIN_Y_SPAN / 2);
    dataMax = Math.min(1, mid + MIN_Y_SPAN / 2);
    // If clipping at the [0,1] edge ate into the span, push the other side.
    if (dataMax - dataMin < MIN_Y_SPAN) {
      if (dataMin === 0) dataMax = Math.min(1, MIN_Y_SPAN);
      else if (dataMax === 1) dataMin = Math.max(0, 1 - MIN_Y_SPAN);
    }
  }
  const span = dataMax - dataMin || 1;

  const xScale =
    epochs.length === 1
      ? () => plotLeft + plotWidth / 2
      : (i: number) => plotLeft + (i / (epochs.length - 1)) * plotWidth;
  const yScale = (v: number) => plotBottom - ((v - dataMin) / span) * plotHeight;

  const points = epochs
    .map((_, i) => `${xScale(i).toFixed(1)},${yScale(f1Values[i]).toFixed(1)}`)
    .join(" ");

  return { xScale, yScale, points, dataMin, dataMax };
}

/** Count valid numeric values for a metric across the epoch series. */
export function countMetricValues(
  epochs: TrainingMetricsEpoch[],
  metricKey: TrainingMetricKey
): number {
  return epochs.reduce(
    (count, epoch) =>
      Number.isFinite(epoch.metrics?.[metricKey]) ? count + 1 : count,
    0
  );
}

/**
 * Build an SVG path for a metric, preserving gaps when a metric is missing.
 * This avoids visually connecting unrelated runs through absent values.
 */
export function buildMetricPath(
  epochs: TrainingMetricsEpoch[],
  metricKey: TrainingMetricKey,
  xScale: (i: number) => number,
  yScale: (v: number) => number
): string {
  let hasOpenSegment = false;
  const commands: string[] = [];

  epochs.forEach((epoch, i) => {
    const value = epoch.metrics?.[metricKey];
    if (!Number.isFinite(value)) {
      hasOpenSegment = false;
      return;
    }

    commands.push(
      `${hasOpenSegment ? "L" : "M"}${xScale(i).toFixed(1)},${yScale(
        value as number
      ).toFixed(1)}`
    );
    hasOpenSegment = true;
  });

  return commands.join(" ");
}

/**
 * Map a click's clientX to the nearest epoch index. Clicks in the left/right
 * padding (or fully outside the SVG) clamp to the first/last epoch.
 */
export function clickXToEpochIndex(
  clientX: number,
  rectLeft: number,
  rectWidth: number,
  viewBoxWidth: number,
  padX: number,
  epochCount: number
): number {
  if (epochCount === 0) return 0;
  if (epochCount === 1) return 0;
  if (rectWidth <= 0) return 0;
  const xPx = clientX - rectLeft;
  const vbX = (xPx / rectWidth) * viewBoxWidth;
  const plotLeft = padX;
  const plotRight = viewBoxWidth - padX;
  const t = (vbX - plotLeft) / (plotRight - plotLeft);
  const clampedT = Math.max(0, Math.min(1, t));
  return Math.round(clampedT * (epochCount - 1));
}

export interface AxisLabel {
  /** Stable React key. */
  key: string;
  /** SVG x in user units. */
  x: number;
  /** Text to render. */
  text: string;
}

/**
 * Pick a sparse set of axis labels: first, last, and best (when distinct).
 * Best label is suppressed if it would visually collide with first or last.
 */
export function computeAxisLabels(
  epochs: TrainingMetricsEpoch[],
  bestIdx: number,
  xScale: (i: number) => number,
  minCollisionPx: number
): AxisLabel[] {
  if (epochs.length === 0) return [];
  const lastIdx = epochs.length - 1;
  const labels: AxisLabel[] = [
    { key: "first", x: xScale(0), text: String(epochs[0].epoch) },
  ];
  if (lastIdx > 0) {
    labels.push({
      key: "last",
      x: xScale(lastIdx),
      text: String(epochs[lastIdx].epoch),
    });
  }
  if (bestIdx > 0 && bestIdx < lastIdx) {
    const bx = xScale(bestIdx);
    const firstX = labels[0].x;
    const lastX = labels.length > 1 ? labels[1].x : firstX;
    const tooCloseToFirst = Math.abs(bx - firstX) < minCollisionPx;
    const tooCloseToLast = Math.abs(bx - lastX) < minCollisionPx;
    if (!tooCloseToFirst && !tooCloseToLast) {
      labels.push({ key: "best", x: bx, text: String(epochs[bestIdx].epoch) });
    }
  }
  return labels;
}

/** Choose svg `textAnchor` based on label position relative to edges. */
export function axisLabelAnchor(
  x: number,
  viewBoxWidth: number,
  padX: number
): "start" | "end" | "middle" {
  if (x < padX + 20) return "start";
  if (x > viewBoxWidth - padX - 20) return "end";
  return "middle";
}
