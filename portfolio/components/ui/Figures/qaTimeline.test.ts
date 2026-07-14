import type { TraceStep } from "../../../hooks/qaTypes";
import { buildTimelineLayout } from "./qaTimeline";

const makeStep = (step: Partial<TraceStep>): TraceStep => ({
  type: "tools",
  content: "Tool",
  ...step,
});

describe("buildTimelineLayout", () => {
  it("places overlapping tool calls on parallel lanes", () => {
    const trace = [
      makeStep({
        type: "plan",
        content: "Plan",
        startOffsetMs: 0,
        durationMs: 1000,
      }),
      makeStep({
        content: "search_receipts",
        startOffsetMs: 1000,
        durationMs: 2000,
      }),
      makeStep({
        content: "search_receipt_descriptions",
        startOffsetMs: 1000,
        durationMs: 1000,
      }),
      makeStep({
        type: "synthesize",
        content: "Answer",
        startOffsetMs: 3000,
        durationMs: 1000,
      }),
    ];

    const layout = buildTimelineLayout(trace, 1000);

    expect(layout.usesAbsoluteTiming).toBe(true);
    expect(layout.laneCount).toBe(2);
    expect(layout.hasParallelTools).toBe(true);
    expect(layout.totalMs).toBe(4000);
    expect(layout.segments.map((segment) => segment.lane)).toEqual([
      0, 0, 1, 0,
    ]);
  });

  it("keeps legacy traces on one sequential lane", () => {
    const trace = [
      makeStep({ type: "plan", content: "Plan", durationMs: 500 }),
      makeStep({ content: "search_receipts", durationMs: 1500 }),
    ];

    const layout = buildTimelineLayout(trace, 1000);

    expect(layout.usesAbsoluteTiming).toBe(false);
    expect(layout.laneCount).toBe(1);
    expect(layout.hasParallelTools).toBe(false);
    expect(layout.totalMs).toBe(2000);
    expect(layout.segments.map((segment) => segment.startMs)).toEqual([0, 500]);
  });
});
