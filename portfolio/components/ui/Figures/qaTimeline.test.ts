import type { TraceStep } from "../../../hooks/qaTypes";
import {
  buildTimelineLayout,
  buildTimelinePlayback,
  getActiveTimelineStepIndices,
  getTimelineRevealPercent,
} from "./qaTimeline";

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
    expect(layout.parallelStepIndices).toEqual([1, 2]);
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
    expect(layout.parallelStepIndices).toEqual([]);
    expect(layout.totalMs).toBe(2000);
    expect(layout.segments.map((segment) => segment.startMs)).toEqual([0, 500]);
  });

  it("preserves overlapping intervals when scaling absolute playback", () => {
    const trace = [
      makeStep({ type: "plan", startOffsetMs: 0, durationMs: 1000 }),
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
        startOffsetMs: 3000,
        durationMs: 1000,
      }),
    ];
    const layout = buildTimelineLayout(trace, 1000);

    const playback = buildTimelinePlayback(layout, {
      targetTotalMs: 12000,
      motionScale: 0.5,
      minStepMs: 600,
      maxStepMs: 3500,
    });

    expect(playback.totalMs).toBe(6000);
    expect(playback.segments[1]).toMatchObject({
      startMs: 1500,
      durationMs: 3000,
      endMs: 4500,
    });
    expect(playback.segments[2]).toMatchObject({
      startMs: 1500,
      durationMs: 1500,
      endMs: 3000,
    });
    expect(getActiveTimelineStepIndices(playback.segments, 2000)).toEqual([
      1, 2,
    ]);
  });

  it("maps clamped sequential playback back to the raw timeline geometry", () => {
    const trace = [
      makeStep({ type: "plan", durationMs: 100 }),
      makeStep({ type: "synthesize", durationMs: 10_000 }),
    ];
    const layout = buildTimelineLayout(trace, 1000);
    const playback = buildTimelinePlayback(layout, {
      targetTotalMs: 12_000,
      motionScale: 1,
      minStepMs: 600,
      maxStepMs: 3500,
    });

    expect(playback.segments.map((segment) => segment.durationMs)).toEqual([
      600, 3500,
    ]);
    expect(getTimelineRevealPercent(layout, playback, 600)).toBeCloseTo(
      (100 / 10_100) * 100,
    );
    expect(getTimelineRevealPercent(layout, playback, 4100)).toBe(100);
  });
});
