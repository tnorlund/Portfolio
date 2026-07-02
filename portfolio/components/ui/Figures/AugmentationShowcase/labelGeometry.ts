/**
 * Pure geometry/label helpers for the AugmentationShowcase figure.
 *
 * The showcase label files (`public/synthetic-receipts/showcase/*.labels.json`)
 * are LayoutLM token-classification records: parallel `tokens`, `bboxes`, and
 * `ner_tags` arrays. Bboxes are `[x0, y0, x1, y1]` normalized to 0-1000 with
 * the y axis pointing UP (y=1000 is the top of the receipt), so converting to
 * CSS percentages flips y.
 */

import { LABEL_COLORS } from "../labelStyles";

export interface ShowcaseLabelFile {
  tokens: string[];
  bboxes: number[][];
  ner_tags: string[];
  merchant_name?: string;
  receipt_key?: string;
  metadata?: {
    operation?: string;
    base_receipt_key?: string;
  };
}

/** CSS-percent rectangle relative to the receipt image. */
export interface CssRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface LabelBox {
  index: number;
  token: string;
  family: string;
  rect: CssRect;
}

/** `B-PRODUCT_NAME` / `I-PRODUCT_NAME` -> `PRODUCT_NAME`; `O` -> null. */
export const familyOf = (tag: string): string | null => {
  if (!tag || tag === "O") {
    return null;
  }
  return tag.replace(/^[BI]-/, "");
};

/** LayoutLM 0-1000 y-up box -> CSS percent rect (y-down). */
export const toCssRect = (bbox: number[]): CssRect => {
  const [x0, y0, x1, y1] = bbox;
  return {
    left: x0 / 10,
    top: (1000 - y1) / 10,
    width: (x1 - x0) / 10,
    height: (y1 - y0) / 10,
  };
};

/** One positioned box per labeled (non-O) token. */
export const buildLabelBoxes = (file: ShowcaseLabelFile): LabelBox[] => {
  const boxes: LabelBox[] = [];
  file.tokens.forEach((token, index) => {
    const family = familyOf(file.ner_tags[index]);
    const bbox = file.bboxes[index];
    if (!family || !bbox || bbox.length !== 4) {
      return;
    }
    boxes.push({ index, token, family, rect: toCssRect(bbox) });
  });
  return boxes;
};

/** Distinct label families in order of first appearance. */
export const familiesIn = (file: ShowcaseLabelFile): string[] => {
  const seen = new Set<string>();
  const ordered: string[] = [];
  file.ner_tags.forEach((tag) => {
    const family = familyOf(tag);
    if (family && !seen.has(family)) {
      seen.add(family);
      ordered.push(family);
    }
  });
  return ordered;
};

/**
 * Token indices to highlight for an augmentation variant.
 *
 * The remove variants derive from different base receipts than the displayed
 * base, so cross-variant token diffing is unsound. Highlights instead come
 * from the manifest itself:
 *  - the recomputed total: GRAND_TOTAL-family tokens containing `newTotal`
 *  - for add operations, the injected item: tokens matching `itemWords`
 *    (the removed line is absent from a remove render, so there is nothing
 *    to outline for removes beyond the new total).
 */
export const findHighlightIndices = (
  file: ShowcaseLabelFile,
  args: {
    operation: string;
    itemWords: string[];
    newTotal: string | null;
  },
): number[] => {
  const indices: number[] = [];
  const itemSet = new Set(args.itemWords.map((w) => w.toUpperCase()));

  file.tokens.forEach((token, index) => {
    const family = familyOf(file.ner_tags[index]);
    if (
      args.newTotal &&
      family === "GRAND_TOTAL" &&
      token.includes(args.newTotal)
    ) {
      indices.push(index);
      return;
    }
    if (
      args.operation === "add_line_item" &&
      itemSet.size > 0 &&
      itemSet.has(token.toUpperCase())
    ) {
      indices.push(index);
    }
  });
  return indices;
};

/**
 * The box the viewport should scroll to when an operation is applied: the
 * actual changed line, not just any highlight. Prefers boxes matching the
 * injected item's words (the GRAND_TOTAL highlights include a summary copy
 * that sits far from the edit); among candidates picks the spatially topmost.
 */
export const pickScrollTarget = (
  boxes: LabelBox[],
  highlightIndices: number[],
  itemWords: string[],
): LabelBox | null => {
  const highlightSet = new Set(highlightIndices);
  const highlighted = boxes.filter((box) => highlightSet.has(box.index));
  if (highlighted.length === 0) {
    return null;
  }
  const itemSet = new Set(itemWords.map((w) => w.toUpperCase()));
  const itemBoxes = highlighted.filter((box) =>
    itemSet.has(box.token.toUpperCase()),
  );
  const pool = itemBoxes.length > 0 ? itemBoxes : highlighted;
  return pool.reduce((best, box) =>
    box.rect.top < best.rect.top ? box : best,
  );
};

/**
 * Stable color per label family: the shared receipt-visualization palette
 * (same one LayoutLMBatchVisualization uses), with a theme-var fallback
 * cycle (by first-appearance order) for families the palette doesn't name.
 */
const FALLBACK_PALETTE = [
  "var(--color-cyan)",
  "var(--color-pink)",
  "var(--color-orange)",
  "var(--color-purple)",
  "var(--color-teal)",
  "var(--color-blue)",
];

export const familyColors = (
  families: string[],
): Record<string, string> => {
  const colors: Record<string, string> = {};
  let fallbackIdx = 0;
  families.forEach((family) => {
    colors[family] =
      LABEL_COLORS[family] ??
      FALLBACK_PALETTE[fallbackIdx++ % FALLBACK_PALETTE.length];
  });
  return colors;
};
