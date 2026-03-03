import { LabelEvaluatorReceipt, LabelEvaluatorEvaluation } from "../../../../types/api";

export interface RevealedDecision {
  key: string;
  tier: 1 | 2;
  decision: "VALID" | "INVALID" | "NEEDS_REVIEW";
  wordText: string;
  lineId: number;
  wordId: number;
  bbox: { x: number; y: number; width: number; height: number };
}

export type Phase = "idle" | "scanning" | "complete";

export interface TierState {
  tier1: number; // 0-100
  tier2: number; // 0-100
}

export interface TierConfig {
  name: string;
  color: string;
  type: string; // e.g. "Deterministic", "Consensus"
}

export interface CarouselConfig {
  tier1: TierConfig;
  tier2: TierConfig;
  getEvaluation: (receipt: LabelEvaluatorReceipt) => LabelEvaluatorEvaluation;
  scanLineColors: [string, string]; // [tier1Color, tier2Color]
}
