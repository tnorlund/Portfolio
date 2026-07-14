import type { TraceStep } from "../../../hooks/qaTypes";

export interface TimelineSegment {
  stepIndex: number;
  startMs: number;
  durationMs: number;
  endMs: number;
  lane: number;
}

export interface TimelineLayout {
  segments: TimelineSegment[];
  totalMs: number;
  laneCount: number;
  usesAbsoluteTiming: boolean;
  hasParallelTools: boolean;
}

const getDuration = (step: TraceStep, defaultStepMs: number): number =>
  step.durationMs != null && step.durationMs > 0
    ? step.durationMs
    : defaultStepMs;

export const buildTimelineLayout = (
  trace: TraceStep[],
  defaultStepMs: number,
): TimelineLayout => {
  const usesAbsoluteTiming =
    trace.length > 0 &&
    trace.every(
      (step) =>
        step.startOffsetMs != null && Number.isFinite(step.startOffsetMs),
    );

  const rawSegments: Omit<TimelineSegment, "lane">[] = [];
  let sequentialStart = 0;

  trace.forEach((step, stepIndex) => {
    const durationMs = getDuration(step, defaultStepMs);
    const startMs = usesAbsoluteTiming
      ? Math.max(0, step.startOffsetMs ?? 0)
      : sequentialStart;

    rawSegments.push({
      stepIndex,
      startMs,
      durationMs,
      endMs: startMs + durationMs,
    });
    sequentialStart += durationMs;
  });

  const laneEnds: number[] = [];
  const laneByStepIndex = new Map<number, number>();
  const chronologicalSegments = [...rawSegments].sort(
    (a, b) => a.startMs - b.startMs || a.stepIndex - b.stepIndex,
  );

  for (const segment of chronologicalSegments) {
    let lane = laneEnds.findIndex((endMs) => endMs <= segment.startMs);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(segment.endMs);
    } else {
      laneEnds[lane] = segment.endMs;
    }
    laneByStepIndex.set(segment.stepIndex, lane);
  }

  const segments = rawSegments.map((segment) => ({
    ...segment,
    lane: laneByStepIndex.get(segment.stepIndex) ?? 0,
  }));
  const totalMs = Math.max(
    defaultStepMs,
    ...segments.map((segment) => segment.endMs),
  );
  const toolSegments = segments.filter(
    (segment) => trace[segment.stepIndex]?.type === "tools",
  );
  const hasParallelTools = toolSegments.some((segment, index) =>
    toolSegments.some(
      (candidate, candidateIndex) =>
        candidateIndex !== index &&
        candidate.startMs < segment.endMs &&
        candidate.endMs > segment.startMs,
    ),
  );

  return {
    segments,
    totalMs,
    laneCount: Math.max(1, laneEnds.length),
    usesAbsoluteTiming,
    hasParallelTools,
  };
};
