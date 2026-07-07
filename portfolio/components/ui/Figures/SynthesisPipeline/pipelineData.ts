/**
 * Static description of the SynthesisPipeline assets and copy.
 *
 * Acts 1-8 run a single merchant (Sprouts) end to end, reading from
 * `public/synthetic-receipts/pipeline/sprouts/`. The closing finale act fans
 * out to every merchant's printed receipt to make the generalization point.
 * Some assets are generated incrementally; helpers here point at the spec'd
 * paths and the acts degrade gracefully when a file is missing.
 */

import { CloudGeom, GlyphSkeleton } from "./geometry";
import { ShowcaseLabelFile } from "../AugmentationShowcase/labelGeometry";

export type Merchant =
  | "sprouts"
  | "costco"
  | "vons"
  | "traderjoes"
  | "cvs"
  | "target";

/** Every merchant the finale fans out to, in card order. */
export const MERCHANTS: Merchant[] = [
  "sprouts",
  "costco",
  "vons",
  "traderjoes",
  "cvs",
  "target",
];

/** The single merchant acts 1-8 are built from. */
export const PIPELINE_MERCHANT: Merchant = "sprouts";

export const MERCHANT_LABELS: Record<Merchant, string> = {
  sprouts: "Sprouts",
  costco: "Costco",
  vons: "Vons",
  traderjoes: "Trader Joe's",
  cvs: "CVS",
  target: "Target",
};

export const PIPELINE_BASE = "/synthetic-receipts/pipeline";

/** How many individual character prints were exported per merchant. */
export const CHAR_PRINT_COUNT = 30;

/** The 94 printable-ASCII codepoints (33..126) rendered into the font grid. */
export const FONT_CODEPOINTS: number[] = Array.from(
  { length: 126 - 33 + 1 },
  (_, i) => 33 + i,
);

export const assetRoot = (merchant: Merchant): string =>
  `${PIPELINE_BASE}/${merchant}`;

export const charPrintSrc = (merchant: Merchant, i: number): string =>
  `${assetRoot(merchant)}/char_prints/${i}.png`;

export const charCloudSrc = (merchant: Merchant): string =>
  `${assetRoot(merchant)}/char_cloud.png`;

export const skeletonSrc = (merchant: Merchant): string =>
  `${assetRoot(merchant)}/char_skeleton.json`;

export const dotParamsSrc = (merchant: Merchant): string =>
  `${assetRoot(merchant)}/dot_params.json`;

export const fontGlyphSrc = (merchant: Merchant, codepoint: number): string =>
  `${assetRoot(merchant)}/font_grid/${codepoint}.png`;

export const styleAnnotatedSrc = (merchant: Merchant): string =>
  `${assetRoot(merchant)}/style_annotated.json`;

export const styleCropSrc = (merchant: Merchant, section: string): string =>
  `${assetRoot(merchant)}/style_crops/${section}.png`;

export const composeStepsSrc = (merchant: Merchant): string =>
  `${assetRoot(merchant)}/compose_steps.json`;

export const finalSrc = (merchant: Merchant): string =>
  `${assetRoot(merchant)}/final.webp`;

/** The real scanned receipt, normalized to the SAME 760-wide box as final.webp
 *  (so real and synth align 1:1 for the finale before/after wipe). */
export const realSrc = (merchant: Merchant): string =>
  `${assetRoot(merchant)}/real.webp`;

/**
 * True pixel dimensions of each merchant's normalized receipt (real + final
 * share these). All 760px wide; heights differ — that difference is the point
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
};

/** Trimmed alpha-mask logo mark, rendered in currentColor (theme-aware). The
 * finale renders it into a fixed box with mask-size:contain, so no per-logo
 * dimensions are needed (aspect ratios vary widely — Trader Joe's/CVS are long
 * wordmarks). */
export const logoSrc = (merchant: Merchant): string =>
  `${assetRoot(merchant)}/logo.png`;

export const finalLabelsSrc = (merchant: Merchant): string =>
  `${assetRoot(merchant)}/final.labels.json`;

export const realThumbSrc = (merchant: Merchant, i: number): string =>
  `${assetRoot(merchant)}/real_thumbs/${i}.webp`;

/** How many real-scan thumbnails act 1 fans out (falls back if absent). */
export const REAL_THUMB_COUNT = 3;

/** Shape of `dot_params.json`. */
export interface DotParams {
  dotSize: number;
  refCap: number;
  weightDefault: number;
  weightBold: number;
  hero: string;
  samples: number;
  /** Cloud PNG pixel geometry for act-3/4 alignment (added incrementally). */
  cloudGeom?: CloudGeom;
}

export const DEFAULT_DOT_PARAMS: DotParams = {
  dotSize: 100,
  refCap: 60,
  weightDefault: 1.0,
  weightBold: 1.33,
  hero: "?",
  samples: 0,
};

/** Weight slider bounds (spec: 0.9–1.4, default 1.0). */
export const WEIGHT_MIN = 0.9;
export const WEIGHT_MAX = 1.4;
export const WEIGHT_STEP = 0.01;

/**
 * Schema for `style_annotated.json`: measured style treatments per receipt
 * section. `name` is a stable machine key, `display` the cited measured claim
 * (e.g. "Underlined ~41% of the time"), `crop` an optional example image. The
 * remaining measured fields are carried through untouched.
 */
export interface StyleSection {
  name: string;
  display: string;
  crop?: string;
  sizeScale?: number;
  weight?: string;
  underline?: boolean | string;
  underlineRate?: number;
  notes?: string;
  match?: string;
}

export interface StyleAnnotated {
  merchant?: string;
  sections: StyleSection[];
}

/**
 * Schema for `compose_steps.json`: the composed receipt's tokens (indices into
 * `final.labels.json` `tokens`) split into reveal groups, in reveal order.
 */
export type ComposeGroupId = "header" | "items" | "summary" | "footer";

export const COMPOSE_GROUP_ORDER: ComposeGroupId[] = [
  "header",
  "items",
  "summary",
  "footer",
];

export const COMPOSE_GROUP_LABELS: Record<ComposeGroupId, string> = {
  header: "Header",
  items: "Line items",
  summary: "Summary",
  footer: "Footer",
};

export interface ComposeSteps {
  groups: Partial<Record<ComposeGroupId, number[]>>;
  tokens_total?: number;
}

/** All fetched-JSON assets for one merchant, filled in progressively. */
export interface MerchantAssets {
  skeleton: GlyphSkeleton | null;
  dotParams: DotParams | null;
  style: StyleAnnotated | null;
  compose: ComposeSteps | null;
  finalLabels: ShowcaseLabelFile | null;
}

export const EMPTY_ASSETS: MerchantAssets = {
  skeleton: null,
  dotParams: null,
  style: null,
  compose: null,
  finalLabels: null,
};

export type ActId = "raw" | "character" | "font" | "assemble" | "finale";

export interface ActMeta {
  id: ActId;
  index: number;
  eyebrow: string;
  headline: string;
  /** Body copy (measured, first person plural). */
  caption: string;
}

/**
 * The eight acts, in scroll order. Copy anchors are lifted verbatim from the
 * spec so the tone stays measured and non-hyped.
 */
export const ACTS: ActMeta[] = [
  {
    id: "raw",
    index: 0,
    eyebrow: "Raw material",
    headline: "It all comes from real receipts",
    caption:
      "No font files exist for these printers. So we mined the letterforms from the receipts themselves. Everything below is derived from these scans.",
  },
  {
    id: "character",
    index: 1,
    eyebrow: "One character",
    headline: "Prints to a pen path to thermal dots",
    caption:
      "140 prints of one character vote into a consensus cloud; we learn the pen path over it — six nodes and their Bézier handles — then stamp thermal dots along it. Bold isn't a second font; it's one parameter: the dot weight.",
  },
  {
    id: "font",
    index: 2,
    eyebrow: "A whole font",
    headline: "Ninety-four glyphs, same method",
    caption:
      "94 glyphs, roughly six to twelve nodes each, mined from the same receipts.",
  },
  {
    id: "assemble",
    index: 3,
    eyebrow: "The receipt assembles",
    headline: "It types itself out, then labels itself",
    caption:
      "Header, items, summary, and footer type out at once from the merchant's grammar; then every word's ground-truth box snaps on. A labeled training example, with zero manual labels.",
  },
  {
    id: "finale",
    index: 4,
    eyebrow: "Same machine, every store",
    headline: "Same machine, six merchants",
    caption:
      "The same machine minted all six: fonts, logos, and styles, mined from each merchant's own receipts.",
  },
];

export const ACT_COUNT = ACTS.length;

/**
 * Per-act autoplay dwell (ms) — how long each act takes to animate through
 * before advancing. Longer for the acts that reveal a lot (font cascade, the
 * final print + labels), shorter for the quick establishing shots.
 */
export const ACT_DWELL_MS: Record<ActId, number> = {
  raw: 4000,
  character: 9500, // prints -> pen path + handles -> thermal dots, one beat
  font: 6500, // the hero glyph flies into the atlas — give it room
  assemble: 11000, // parallel section typing, then the label boxes draw on
  finale: 18000, // slow gallery auto-pan through all the merchant pairs
};

/** How long autoplay stays paused after a manual interaction, then resumes. */
export const AUTOPLAY_IDLE_RESUME_MS = 10000;

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
};
