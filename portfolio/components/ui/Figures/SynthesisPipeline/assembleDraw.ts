/**
 * Pure helpers for the assemble-act typing reveal.
 * Kept free of React/canvas so incremental draw logic is unit-testable.
 */

export type WordRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

/** How many words each group should show at typing progress `t` (0..1). */
export const revealedCountsForProgress = (
  groups: number[][],
  t: number,
): number[] => {
  const clamped = Math.min(1, Math.max(0, t));
  return groups.map((g) => Math.round(clamped * g.length));
};

/**
 * Word indices newly revealed since `prevCounts` (exclusive) up to `nextCounts`
 * (inclusive). Used to draw only the delta instead of re-cropping every word.
 */
export const newlyRevealedWordIndices = (
  groups: number[][],
  prevCounts: number[],
  nextCounts: number[],
): number[] => {
  const out: number[] = [];
  for (let g = 0; g < groups.length; g += 1) {
    const prev = prevCounts[g] ?? 0;
    const next = nextCounts[g] ?? 0;
    const group = groups[g];
    for (let i = prev; i < next && i < group.length; i += 1) {
      out.push(group[i]);
    }
  }
  return out;
};

/** Convert a %-space rect into pixel crop args for drawImage. */
export const rectToCrop = (
  rect: WordRect,
  renderW: number,
  renderH: number,
): { sx: number; sy: number; sw: number; sh: number } | null => {
  const sx = (rect.left / 100) * renderW;
  const sy = (rect.top / 100) * renderH;
  const sw = (rect.width / 100) * renderW;
  const sh = (rect.height / 100) * renderH;
  if (sw <= 0 || sh <= 0) {
    return null;
  }
  return { sx, sy, sw, sh };
};

/** Caret blink on for this typing progress (matches prior 48-phase parity). */
export const caretVisibleAt = (t: number): boolean =>
  t > 0 && t < 1 && Math.floor(t * 48) % 2 === 0;
