/**
 * Pure geometry/label helpers for the AugmentationShowcase figure.
 *
 * The showcase label files (`public/synthetic-receipts/showcase/*.labels.json`)
 * are LayoutLM token-classification records: parallel `tokens`, `bboxes`, and
 * `ner_tags` arrays. Bboxes are `[x0, y0, x1, y1]` normalized to 0-1000 with
 * the y axis pointing UP (y=1000 is the top of the receipt), so converting to
 * CSS percentages flips y.
 */

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
 * Stable color per label family: fixed hues for the families that carry the
 * story, palette fallback (by first-appearance order) for the rest.
 */
const FIXED_FAMILY_COLORS: Record<string, string> = {
  PRODUCT_NAME: "#43A047",
  LINE_TOTAL: "#1E88E5",
  GRAND_TOTAL: "#E53935",
  UNIT_PRICE: "#00ACC1",
  QUANTITY: "#7CB342",
  MERCHANT_NAME: "#8E24AA",
  ADDRESS_LINE: "#FB8C00",
  DATE: "#FDD835",
  TIME: "#F4B400",
  PAYMENT_METHOD: "#5E35B1",
};

const FALLBACK_PALETTE = [
  "#26A69A",
  "#EC407A",
  "#78909C",
  "#9CCC65",
  "#FF7043",
  "#5C6BC0",
];

export const familyColors = (
  families: string[],
): Record<string, string> => {
  const colors: Record<string, string> = {};
  let fallbackIdx = 0;
  families.forEach((family) => {
    colors[family] =
      FIXED_FAMILY_COLORS[family] ??
      FALLBACK_PALETTE[fallbackIdx++ % FALLBACK_PALETTE.length];
  });
  return colors;
};
