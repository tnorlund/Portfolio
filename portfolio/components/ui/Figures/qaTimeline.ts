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
  parallelStepIndices: number[];
}

export interface TimelinePlaybackOptions {
  targetTotalMs: number;
  motionScale: number;
  minStepMs: number;
  maxStepMs: number;
}

export interface TimelinePlayback {
  segments: TimelineSegment[];
  totalMs: number;
  boundariesMs: number[];
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
  const parallelStepIndices = toolSegments
    .filter((segment, index) =>
      toolSegments.some(
        (candidate, candidateIndex) =>
          candidateIndex !== index &&
          candidate.startMs < segment.endMs &&
          candidate.endMs > segment.startMs,
      ),
    )
    .map((segment) => segment.stepIndex);

  return {
    segments,
    totalMs,
    laneCount: Math.max(1, laneEnds.length),
    usesAbsoluteTiming,
    hasParallelTools: parallelStepIndices.length > 0,
    parallelStepIndices,
  };
};

export const buildTimelinePlayback = (
  layout: TimelineLayout,
  options: TimelinePlaybackOptions,
): TimelinePlayback => {
  const { targetTotalMs, motionScale, minStepMs, maxStepMs } = options;
  let segments: TimelineSegment[];

  if (layout.usesAbsoluteTiming) {
    const scale = (targetTotalMs * motionScale) / layout.totalMs;
    segments = layout.segments.map((segment) => {
      const startMs = Math.round(segment.startMs * scale);
      const durationMs = Math.round(segment.durationMs * scale);
      return {
        ...segment,
        startMs,
        durationMs,
        endMs: startMs + durationMs,
      };
    });
  } else {
    const rawTotalMs = layout.segments.reduce(
      (total, segment) => total + segment.durationMs,
      0,
    );
    const durationScale = rawTotalMs > 0 ? targetTotalMs / rawTotalMs : 1;
    let startMs = 0;

    segments = layout.segments.map((segment) => {
      const durationMs = Math.max(
        minStepMs * motionScale,
        Math.min(
          maxStepMs * motionScale,
          Math.round(segment.durationMs * durationScale * motionScale),
        ),
      );
      const playbackSegment = {
        ...segment,
        startMs,
        durationMs,
        endMs: startMs + durationMs,
      };
      startMs += durationMs;
      return playbackSegment;
    });
  }

  const totalMs = Math.max(0, ...segments.map((segment) => segment.endMs));
  const boundariesMs = Array.from(
    new Set([
      0,
      totalMs,
      ...segments.flatMap((segment) => [segment.startMs, segment.endMs]),
    ]),
  ).sort((a, b) => a - b);

  return { segments, totalMs, boundariesMs };
};

export const getActiveTimelineStepIndices = (
  segments: TimelineSegment[],
  elapsedMs: number,
): number[] =>
  segments
    .filter(
      (segment) => segment.startMs <= elapsedMs && segment.endMs > elapsedMs,
    )
    .map((segment) => segment.stepIndex);

export const getTimelineRevealPercent = (
  layout: TimelineLayout,
  playback: TimelinePlayback,
  boundaryMs: number,
): number => {
  if (layout.totalMs <= 0 || playback.totalMs <= 0) return 0;

  if (layout.usesAbsoluteTiming) {
    return (boundaryMs / playback.totalMs) * 100;
  }

  const completedStepIndices = new Set(
    playback.segments
      .filter((segment) => segment.endMs <= boundaryMs)
      .map((segment) => segment.stepIndex),
  );
  const revealedEndMs = layout.segments.reduce(
    (furthestEndMs, segment) =>
      completedStepIndices.has(segment.stepIndex)
        ? Math.max(furthestEndMs, segment.endMs)
        : furthestEndMs,
    0,
  );

  return (revealedEndMs / layout.totalMs) * 100;
};
