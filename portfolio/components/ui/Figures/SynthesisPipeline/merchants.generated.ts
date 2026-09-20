// GENERATED FILE - do not edit by hand.
// Source: tools/glyph-studio/fixtures/pipeline_merchants.json
// Regenerate: python -m glyphstudio.portfolio_wiring
//   (tools/glyph-studio/py/new_vendor.py wire does this after an export)

export type Merchant =
  | "sprouts"
  | "costco"
  | "vons"
  | "traderjoes"
  | "cvs"
  | "target"
  | "innout"
  | "wildfork"
  | "speedway"
  | "wholefoods"
  | "roastrice";

/** Every merchant the finale fans out to, in card order. */
export const MERCHANTS: Merchant[] = [
  "sprouts",
  "costco",
  "vons",
  "traderjoes",
  "cvs",
  "target",
  "innout",
  "wildfork",
  "speedway",
  "wholefoods",
  "roastrice",
];

export const MERCHANT_LABELS: Record<Merchant, string> = {
  sprouts: "Sprouts",
  costco: "Costco",
  vons: "Vons",
  traderjoes: "Trader Joe's",
  cvs: "CVS",
  target: "Target",
  innout: "In-N-Out",
  wildfork: "Wild Fork",
  speedway: "Speedway",
  wholefoods: "Whole Foods",
  roastrice: "Roast & Rice",
};

/**
 * True pixel dimensions of each merchant's normalized receipt (real + final
 * share these). All 760px wide; heights differ - that difference is the point
 * of the finale, so the cards render at a common width and their natural
 * (different) heights, tops aligned.
 */
export const RECEIPT_DIMS: Record<Merchant, { w: number; h: number }> = {
  sprouts: { w: 760, h: 2471 },
  costco: { w: 760, h: 2999 },
  vons: { w: 760, h: 2732 },
  traderjoes: { w: 760, h: 2023 },
  cvs: { w: 760, h: 2771 },
  target: { w: 760, h: 1878 },
  innout: { w: 760, h: 1958 },
  wildfork: { w: 760, h: 2678 },
  speedway: { w: 760, h: 1711 },
  wholefoods: { w: 760, h: 1559 },
  roastrice: { w: 760, h: 1300 },
};

/**
 * The measured-weight callout for act 4, per merchant (spec copy). Shown when
 * the slider reaches the merchant's bold weight.
 */
export const BOLD_WEIGHT_CALLOUT: Record<Merchant, string> = {
  sprouts: "the measured BALANCE DUE weight",
  costco: "the chart heavy face",
  vons: "the measured heading weight",
  traderjoes: "the measured heading weight",
  cvs: "the measured heading weight",
  target: "the measured department heading weight",
  innout: "the measured heading weight",
  wildfork: "the measured heading weight",
  speedway: "the measured DEBIT tender-line weight",
  wholefoods: "the measured heading weight",
  roastrice: "the measured Total Due weight",
};
