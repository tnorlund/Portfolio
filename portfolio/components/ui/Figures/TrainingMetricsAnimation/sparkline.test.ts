import {
  computeScales,
  clickXToEpochIndex,
  computeAxisLabels,
  axisLabelAnchor,
  buildMetricPath,
  countMetricValues,
  type SparklineDims,
} from "./sparkline";
import type { TrainingMetricsEpoch } from "../../../../types/api";

const DIMS: SparklineDims = {
  width: 600,
  height: 80,
  padX: 8,
  padTop: 18,
  padBottom: 14,
};

const e = (epoch: number, val_f1: number, is_best = false): TrainingMetricsEpoch =>
  ({
    epoch,
    is_best,
    metrics: { val_f1 } as any,
    per_label: {},
    confusion_matrix: undefined,
  } as unknown as TrainingMetricsEpoch);

describe("computeScales", () => {
  test("empty epochs returns degenerate scales without throwing", () => {
    const s = computeScales([], DIMS);
    expect(s.points).toBe("");
    expect(s.xScale(0)).toBe(DIMS.padX);
    expect(s.yScale(0.5)).toBe(DIMS.padTop);
  });

  test("single epoch centers x at plot midpoint", () => {
    const s = computeScales([e(1, 0.8)], DIMS);
    const plotMid = DIMS.padX + (DIMS.width - 2 * DIMS.padX) / 2;
    expect(s.xScale(0)).toBeCloseTo(plotMid);
  });

  test("two-epoch curve spans plot width", () => {
    const s = computeScales([e(1, 0.0), e(2, 1.0)], DIMS);
    expect(s.xScale(0)).toBe(DIMS.padX);
    expect(s.xScale(1)).toBe(DIMS.width - DIMS.padX);
  });

  test("min y-span floor expands flat-line runs", () => {
    const flat = Array.from({ length: 5 }, (_, i) => e(i + 1, 0.79));
    const s = computeScales(flat, DIMS);
    // Without the floor, dataMin and dataMax would both be near 0.79 ± 0.05.
    // The floor forces a span of at least 0.1, so dataMax-dataMin >= 0.1.
    expect(s.dataMax - s.dataMin).toBeGreaterThanOrEqual(0.1 - 1e-9);
  });

  test("y span clamps to [0, 1]", () => {
    const s = computeScales([e(1, 0.0), e(2, 1.0)], DIMS);
    expect(s.dataMin).toBeGreaterThanOrEqual(0);
    expect(s.dataMax).toBeLessThanOrEqual(1);
  });

  test("missing val_f1 collapses to 0 (drawn as dip, not gap)", () => {
    const epochs = [
      e(1, 0.5),
      { epoch: 2, is_best: false, metrics: {}, per_label: {} } as any,
      e(3, 0.5),
    ];
    const s = computeScales(epochs, DIMS);
    // Polyline should have 3 points (no gap support yet — intentional).
    expect(s.points.split(" ")).toHaveLength(3);
  });

  test("optional metric keys expand the shared ratio scale", () => {
    const epochs = [
      {
        ...e(1, 0.8),
        metrics: { val_f1: 0.8, val_precision: 0.95, val_recall: 0.7 },
      },
      {
        ...e(2, 0.82),
        metrics: { val_f1: 0.82, val_precision: 0.96, val_recall: 0.72 },
      },
    ] as TrainingMetricsEpoch[];
    const f1Only = computeScales(epochs, DIMS);
    const withOverlay = computeScales(epochs, DIMS, [
      "val_f1",
      "val_precision",
      "val_recall",
    ]);

    expect(withOverlay.dataMax).toBeGreaterThan(f1Only.dataMax);
    expect(withOverlay.dataMin).toBeLessThan(f1Only.dataMin);
  });
});

describe("metric path helpers", () => {
  test("countMetricValues ignores missing metrics", () => {
    const epochs = [
      e(1, 0.5),
      { epoch: 2, is_best: false, metrics: {}, per_label: {} } as any,
      { ...e(3, 0.7), metrics: { val_f1: 0.7, val_precision: 0.8 } },
    ] as TrainingMetricsEpoch[];

    expect(countMetricValues(epochs, "val_f1")).toBe(2);
    expect(countMetricValues(epochs, "val_precision")).toBe(1);
  });

  test("buildMetricPath preserves gaps with new move commands", () => {
    const epochs = [
      { ...e(1, 0.5), metrics: { val_f1: 0.5, val_precision: 0.6 } },
      { epoch: 2, is_best: false, metrics: {}, per_label: {} } as any,
      { ...e(3, 0.7), metrics: { val_f1: 0.7, val_precision: 0.8 } },
    ] as TrainingMetricsEpoch[];
    const s = computeScales(epochs, DIMS, ["val_precision"]);
    const path = buildMetricPath(
      epochs,
      "val_precision",
      s.xScale,
      s.yScale
    );

    expect(path.match(/M/g)).toHaveLength(2);
    expect(path).not.toContain("L");
  });
});

describe("clickXToEpochIndex", () => {
  const baseArgs = {
    rectLeft: 0,
    rectWidth: 600,
    viewBoxWidth: 600,
    padX: 8,
  };

  test("clicking center of 11-epoch run picks epoch 5", () => {
    const idx = clickXToEpochIndex(
      300,
      baseArgs.rectLeft,
      baseArgs.rectWidth,
      baseArgs.viewBoxWidth,
      baseArgs.padX,
      11
    );
    expect(idx).toBe(5);
  });

  test("clicking left edge picks epoch 0", () => {
    const idx = clickXToEpochIndex(0, 0, 600, 600, 8, 11);
    expect(idx).toBe(0);
  });

  test("clicking right edge picks last epoch", () => {
    const idx = clickXToEpochIndex(600, 0, 600, 600, 8, 11);
    expect(idx).toBe(10);
  });

  test("clicking left of SVG (negative clientX - rectLeft) clamps to 0", () => {
    const idx = clickXToEpochIndex(-50, 0, 600, 600, 8, 11);
    expect(idx).toBe(0);
  });

  test("clicking right of SVG clamps to last", () => {
    const idx = clickXToEpochIndex(99999, 0, 600, 600, 8, 11);
    expect(idx).toBe(10);
  });

  test("0 epochs returns 0", () => {
    expect(clickXToEpochIndex(300, 0, 600, 600, 8, 0)).toBe(0);
  });

  test("1 epoch returns 0 regardless of click position", () => {
    expect(clickXToEpochIndex(300, 0, 600, 600, 8, 1)).toBe(0);
    expect(clickXToEpochIndex(0, 0, 600, 600, 8, 1)).toBe(0);
  });

  test("0 width returns 0 (defensive)", () => {
    expect(clickXToEpochIndex(300, 0, 0, 600, 8, 11)).toBe(0);
  });

  test("rendered width different from viewBox still maps correctly", () => {
    // Container 1280px wide, viewBox 600 — click at 640 (center) → epoch 5
    const idx = clickXToEpochIndex(640, 0, 1280, 600, 8, 11);
    expect(idx).toBe(5);
  });
});

describe("computeAxisLabels", () => {
  const xScaleLinear = (n: number) => (i: number) =>
    DIMS.padX + (i / (n - 1)) * (DIMS.width - 2 * DIMS.padX);

  test("empty epochs returns no labels", () => {
    expect(computeAxisLabels([], -1, xScaleLinear(1), 30)).toEqual([]);
  });

  test("single epoch returns first only (no last)", () => {
    const labels = computeAxisLabels([e(1, 0.5)], -1, () => 0, 30);
    expect(labels).toHaveLength(1);
    expect(labels[0].key).toBe("first");
  });

  test("best in middle: returns first, last, best", () => {
    const epochs = [e(1, 0.1), e(2, 0.3), e(3, 0.9, true), e(4, 0.7), e(5, 0.6)];
    const labels = computeAxisLabels(epochs, 2, xScaleLinear(5), 30);
    expect(labels.map((l) => l.key)).toEqual(["first", "last", "best"]);
  });

  test("best at index 1 of long run: suppressed because near first", () => {
    const epochs = Array.from({ length: 63 }, (_, i) =>
      e(i + 1, 0.5, i === 1)
    );
    const labels = computeAxisLabels(epochs, 1, xScaleLinear(63), 30);
    expect(labels.find((l) => l.key === "best")).toBeUndefined();
  });

  test("best at last index: not added (collides with last)", () => {
    const epochs = [e(1, 0.1), e(2, 0.5), e(3, 0.9, true)];
    const labels = computeAxisLabels(epochs, 2, xScaleLinear(3), 30);
    // bestIdx === lastIdx fails the `bestIdx < lastIdx` guard, so no best label
    expect(labels.find((l) => l.key === "best")).toBeUndefined();
  });

  test("best in middle of 63-epoch run renders cleanly", () => {
    const epochs = Array.from({ length: 63 }, (_, i) =>
      e(i + 1, 0.5, i === 47)
    );
    const labels = computeAxisLabels(epochs, 47, xScaleLinear(63), 30);
    expect(labels.map((l) => l.key).sort()).toEqual(["best", "first", "last"]);
    const best = labels.find((l) => l.key === "best")!;
    expect(best.text).toBe("48");
  });
});

describe("axisLabelAnchor", () => {
  test("near left edge -> start", () => {
    expect(axisLabelAnchor(10, 600, 8)).toBe("start");
  });
  test("near right edge -> end", () => {
    expect(axisLabelAnchor(590, 600, 8)).toBe("end");
  });
  test("middle -> middle", () => {
    expect(axisLabelAnchor(300, 600, 8)).toBe("middle");
  });
});
