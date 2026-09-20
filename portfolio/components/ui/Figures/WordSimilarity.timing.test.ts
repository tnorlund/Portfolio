import { buildTimingSteps } from "./WordSimilarity";
import type { MilkSimilarityTiming } from "../../../types/api";

// Exact `timing` block served by https://api.tylernorlund.com/word_similarity
// on 2026-09-20 (cache regenerated 2026-09-19T21:04Z). No Chroma fields.
const prodTiming: MilkSimilarityTiming = {
  line_fetch_all_ms: 16678.7,
  filter_lines_ms: 0.3,
  dynamo_fetch_total_ms: 1767.4,
  total_ms: 18786.8,
  parallel_workers: 50,
  dynamo_details: {
    avg_ms: 549.8,
    min_ms: 29.8,
    max_ms: 1332.7,
    count: 91,
    items_returned: 4020,
    sequential_ms: 50033.0,
    speedup: 28.3,
  },
};

test("builds segments from the DynamoDB-era timing shape without Chroma fields", () => {
  const steps = buildTimingSteps(prodTiming);
  expect(steps.map((s) => s.name)).toEqual([
    "Fetch Lines",
    "Filter Lines",
    "Fetch Receipts",
    "Finalize",
  ]);
  for (const step of steps) {
    expect(Number.isFinite(step.ms)).toBe(true);
    expect(() => step.ms.toFixed(1)).not.toThrow();
  }
  const accounted = steps.reduce((sum, s) => sum + s.ms, 0);
  expect(accounted).toBeCloseTo(prodTiming.total_ms, 5);
});

test("still renders the legacy Chroma shape", () => {
  const steps = buildTimingSteps({
    chromadb_init_ms: 100,
    chromadb_fetch_all_ms: 200,
    filter_lines_ms: 5,
    dynamo_fetch_total_ms: 500,
    total_ms: 850,
    parallel_workers: 50,
    use_chroma_cloud: true,
    cloud_connect_ms: 50,
  });
  expect(steps.map((s) => s.name)).toEqual([
    "Open Chroma",
    "Chroma Fetch",
    "Filter Lines",
    "Fetch Receipts",
    "Finalize",
  ]);
});

test("skips undefined, NaN, and zero fields instead of throwing", () => {
  const steps = buildTimingSteps({
    line_fetch_all_ms: Number.NaN,
    filter_lines_ms: 0,
    dynamo_fetch_total_ms: undefined,
    total_ms: 0,
  } as MilkSimilarityTiming);
  expect(steps).toEqual([]);
});
