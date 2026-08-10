import {
  LineItemDemoBand,
  LineItemDemoItem,
  LineItemDemoReceipt,
} from "../../../../types/api";

/**
 * Pure timeline math for the decoder walkthrough, kept out of the
 * component so the stage sequencing is unit-testable without
 * requestAnimationFrame (same discipline as SynthesisPipeline's
 * advanceAutoplay).
 */

export type DecoderStageKey =
  | "zone"
  | "bands"
  | "guards"
  | "pair"
  | "qty"
  | "reconcile";

export interface DecoderStage {
  key: DecoderStageKey;
  title: string;
  duration: number;
}

export const HOLD_DURATION = 2000;
export const TRANSITION_DURATION = 600;

const STAGE_DEFS: Record<
  DecoderStageKey,
  { title: string; duration: number }
> = {
  zone: { title: "Locate the items zone", duration: 1500 },
  bands: { title: "Group words into rows", duration: 2000 },
  guards: { title: "Reject non-products", duration: 1800 },
  pair: { title: "Pair names with prices", duration: 2600 },
  qty: { title: "Recover quantities", duration: 2000 },
  reconcile: { title: "Check the math", duration: 2200 },
};

export const rejectedBands = (
  receipt: LineItemDemoReceipt
): LineItemDemoBand[] =>
  receipt.bands.filter((b) => b.guard || b.role === "OUTSIDE");

export const donorBands = (
  receipt: LineItemDemoReceipt
): LineItemDemoBand[] =>
  receipt.bands.filter(
    (b) => b.outcome === "quantity_donor" || b.outcome === "absorbed"
  );

export const quantityItems = (
  receipt: LineItemDemoReceipt
): LineItemDemoItem[] =>
  receipt.items.filter(
    (i) => i.quantity != null && i.unit_price != null
  );

/** Items sorted top-of-receipt first for display (decoder emits bottom
 * first; y is the normalized bottom-left-origin band center, so larger
 * y = higher on the receipt). */
export const displayItems = (
  receipt: LineItemDemoReceipt
): LineItemDemoItem[] =>
  [...receipt.items].sort((a, b) => (b.y ?? 0) - (a.y ?? 0));

/** Stages relevant to this receipt: zone/bands/pair/reconcile always;
 * guards and qty only when the receipt exercises them. */
export function buildStagePlan(
  receipt: LineItemDemoReceipt
): DecoderStage[] {
  const keys: DecoderStageKey[] = ["zone", "bands"];
  if (rejectedBands(receipt).length > 0) keys.push("guards");
  keys.push("pair");
  if (quantityItems(receipt).length > 0 || donorBands(receipt).length > 0)
    keys.push("qty");
  keys.push("reconcile");
  return keys.map((key) => ({ key, ...STAGE_DEFS[key] }));
}

export interface DecoderFrame {
  /** Index into the stage plan (clamped to the last stage). */
  stageIndex: number;
  stageKey: DecoderStageKey;
  /** 0..1 within the current stage (1 once past it). */
  progress: number;
  phase: "stages" | "hold" | "transition" | "next";
}

export function planDuration(plan: DecoderStage[]): number {
  return plan.reduce((sum, s) => sum + s.duration, 0);
}

/**
 * Where the walkthrough is at `elapsed` ms (already motion-scaled).
 * Phase "next" means the caller should advance to the next receipt.
 */
export function computeFrame(
  plan: DecoderStage[],
  elapsed: number,
  holdDuration: number = HOLD_DURATION,
  transitionDuration: number = TRANSITION_DURATION
): DecoderFrame {
  let start = 0;
  for (let i = 0; i < plan.length; i += 1) {
    const stage = plan[i];
    if (elapsed < start + stage.duration) {
      return {
        stageIndex: i,
        stageKey: stage.key,
        progress: Math.min(1, (elapsed - start) / stage.duration),
        phase: "stages",
      };
    }
    start += stage.duration;
  }
  const last = plan.length - 1;
  const lastKey = plan[last].key;
  if (elapsed < start + holdDuration) {
    return { stageIndex: last, stageKey: lastKey, progress: 1, phase: "hold" };
  }
  if (elapsed < start + holdDuration + transitionDuration) {
    return {
      stageIndex: last,
      stageKey: lastKey,
      progress: 1,
      phase: "transition",
    };
  }
  return { stageIndex: last, stageKey: lastKey, progress: 1, phase: "next" };
}

/** How many entries of an ordered reveal list are visible. */
export function revealCount(
  total: number,
  plan: DecoderStage[],
  frame: DecoderFrame,
  key: DecoderStageKey
): number {
  const idx = plan.findIndex((s) => s.key === key);
  if (idx === -1 || frame.stageIndex > idx) return total;
  if (frame.stageIndex < idx) return 0;
  return Math.min(total, Math.ceil(frame.progress * total));
}

/** True once the stage `key` has fully completed. */
export function stageDone(
  plan: DecoderStage[],
  frame: DecoderFrame,
  key: DecoderStageKey
): boolean {
  const idx = plan.findIndex((s) => s.key === key);
  if (idx === -1) return false;
  return (
    frame.stageIndex > idx ||
    (frame.stageIndex === idx && frame.progress >= 1)
  );
}

/** True once the stage `key` has started (or been passed). */
export function stageStarted(
  plan: DecoderStage[],
  frame: DecoderFrame,
  key: DecoderStageKey
): boolean {
  const idx = plan.findIndex((s) => s.key === key);
  if (idx === -1) return false;
  return frame.stageIndex >= idx;
}
