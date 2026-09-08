/**
 * Decide when the autoplay rAF loop should publish React state.
 * Keeps refs authoritative every frame while limiting reconciles.
 */

export const AUTOPLAY_PROGRESS_MIN_INTERVAL_MS = 33; // ~30fps UI updates
export const AUTOPLAY_PROGRESS_MIN_DELTA = 0.01;

export type PublishDecision = {
  publishAct: boolean;
  publishProgress: boolean;
};

/**
 * `refs` are the latest timeline values; `rendered*` are last values pushed to React.
 * Always publish act changes. Throttle progress to an interval / minimum delta,
 * and always publish progress when an act boundary is crossed.
 */
export const shouldPublishAutoplay = (
  nextAct: number,
  nextProgress: number,
  renderedAct: number,
  renderedProgress: number,
  lastProgressPublishMs: number,
  nowMs: number,
  minIntervalMs: number = AUTOPLAY_PROGRESS_MIN_INTERVAL_MS,
  minDelta: number = AUTOPLAY_PROGRESS_MIN_DELTA,
): PublishDecision => {
  const publishAct = nextAct !== renderedAct;
  if (publishAct) {
    return { publishAct: true, publishProgress: true };
  }
  const delta = Math.abs(nextProgress - renderedProgress);
  const due =
    nowMs - lastProgressPublishMs >= minIntervalMs || delta >= minDelta;
  return { publishAct: false, publishProgress: due && delta > 0 };
};
