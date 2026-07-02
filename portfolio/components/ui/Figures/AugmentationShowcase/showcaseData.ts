/**
 * Static description of the committed showcase assets under
 * `public/synthetic-receipts/showcase/`. Mirrors that directory's
 * `manifest.json` (a test asserts they stay in sync) and adds what the
 * manifest lacks: image dimensions (layout stability) and the injected /
 * removed item words used for highlight matching.
 */

export const SHOWCASE_BASE_PATH = "/synthetic-receipts/showcase";

export type ShowcaseOperation =
  | "base_real_receipt"
  | "add_line_item"
  | "remove_line_item";

export interface ShowcaseVariant {
  id: string;
  operation: ShowcaseOperation;
  /** Short control label, e.g. "+ LIMES". */
  controlLabel: string;
  caption: string;
  itemWords: string[];
  oldTotal: string | null;
  newTotal: string | null;
  width: number;
  height: number;
  labeledTokens: number;
  tokens: number;
}

export const showcaseVariants: ShowcaseVariant[] = [
  {
    id: "base",
    operation: "base_real_receipt",
    controlLabel: "Original",
    caption: "A real Sprouts receipt, rendered photorealistically.",
    itemWords: [],
    oldTotal: null,
    newTotal: null,
    width: 520,
    height: 1691,
    labeledTokens: 47,
    tokens: 181,
  },
  {
    id: "add_line_item_1",
    operation: "add_line_item",
    controlLabel: "+ LIMES",
    caption: "Added LIMES (PRODUCE), total 29.08 → 30.58.",
    itemWords: ["LIMES"],
    oldTotal: "29.08",
    newTotal: "30.58",
    width: 520,
    height: 2425,
    labeledTokens: 61,
    tokens: 203,
  },
  {
    id: "remove_line_item_2",
    operation: "remove_line_item",
    controlLabel: "− SOUR CREAM",
    caption: "Removed SOUR CREAM (DAIRY), total 10.78 → 7.99.",
    itemWords: ["SOUR", "CREAM"],
    oldTotal: "10.78",
    newTotal: "7.99",
    width: 520,
    height: 1691,
    labeledTokens: 44,
    tokens: 178,
  },
];

export const generatedVariantCount = showcaseVariants.filter(
  (v) => v.operation !== "base_real_receipt",
).length;

export const renderSrc = (variant: ShowcaseVariant): string =>
  `${SHOWCASE_BASE_PATH}/${variant.id}.webp`;

export const labelsSrc = (variant: ShowcaseVariant): string =>
  `${SHOWCASE_BASE_PATH}/${variant.id}.labels.json`;
