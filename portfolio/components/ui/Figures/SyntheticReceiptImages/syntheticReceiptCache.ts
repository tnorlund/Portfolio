import reviewTargets from "./syntheticReceiptReviewTargets.json";

export interface SyntheticReceiptImage {
  id: string;
  title: string;
  operation: string;
  imageSrc: string;
  width: number;
  height: number;
  candidateId: string;
  source: string;
  merchantName?: string;
  baseReceiptKey: string;
  trainOnlyReason: string;
  tokenCount: number;
  lineCount: number;
  structureScore?: number;
  oldGrandTotal?: string;
  newGrandTotal?: string;
  labelTarget?: string;
  expectedEffect?: string;
  reviewFocus?: string[];
}

export const syntheticReceiptImages =
  reviewTargets satisfies SyntheticReceiptImage[];
