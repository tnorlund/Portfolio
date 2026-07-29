export type SectionId =
  | "TRANSACTION_INFO"
  | "ITEMS"
  | "SUMMARY"
  | "PAYMENT";

export interface SectionDefinition {
  id: SectionId;
  shortLabel: string;
  label: string;
  color: string;
}

export const SECTIONS: SectionDefinition[] = [
  {
    id: "TRANSACTION_INFO",
    shortLabel: "INFO",
    label: "Transaction info",
    color: "var(--color-blue)",
  },
  {
    id: "ITEMS",
    shortLabel: "ITEMS",
    label: "Items",
    color: "var(--color-purple)",
  },
  {
    id: "SUMMARY",
    shortLabel: "SUMMARY",
    label: "Summary",
    color: "var(--color-orange)",
  },
  {
    id: "PAYMENT",
    shortLabel: "PAYMENT",
    label: "Payment",
    color: "var(--color-green)",
  },
];

export const SECTION_BY_ID = Object.fromEntries(
  SECTIONS.map((section) => [section.id, section]),
) as Record<SectionId, SectionDefinition>;

export const CORPUS_SNAPSHOT = {
  id: "20260729_203457_486164_849816c8",
  dynamodbSyncedAt: "2026-07-29T20:41:05.751096+00:00",
  receipts: 783,
  embeddedRows: 28_172,
  note: "Genuine QA-valid corpus rows and receipt images from the frozen dev snapshot.",
} as const;

export interface BoundingBox {
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
}

export interface ReceiptRow {
  id: string;
  rowId: number;
  text: string;
  truth: SectionId;
  baseline: SectionId;
  hybrid: SectionId;
  sourceTruth: string;
  sourceBaseline: string;
  sourceHybrid: string;
  bbox: BoundingBox;
}

export interface CurrentReceipt {
  sourceId: string;
  merchant: string;
  visibleRows: number;
  sourceRows: number;
  imageId: string;
  imageKey: string;
  width: number;
  height: number;
  rows: ReceiptRow[];
}

/**
 * A crop of one genuine receipt in the untouched test split. Text is the
 * materialized Apple Vision OCR row text. The source labels and predictions
 * are preserved alongside the four display bands.
 */
export const CURRENT_RECEIPT: CurrentReceipt = {
  sourceId: "d47b0f01 · R1",
  merchant: "Salt & Straw",
  visibleRows: 14,
  sourceRows: 24,
  imageId: "d47b0f01-859d-499b-a9b0-4feb312b4d27",
  imageKey: "assets/d47b0f01-859d-499b-a9b0-4feb312b4d27/1.webp",
  width: 425,
  height: 884,
  rows: [
    {
      id: "row-2",
      rowId: 2,
      text: "Salt & Straw",
      truth: "TRANSACTION_INFO",
      baseline: "TRANSACTION_INFO",
      hybrid: "TRANSACTION_INFO",
      sourceTruth: "STOREFRONT",
      sourceBaseline: "STOREFRONT",
      sourceHybrid: "STOREFRONT",
      bbox: { xMin: 0.4316419539420748, xMax: 0.6952462630241061, yMin: 0.871117548766409, yMax: 0.8977778003838407 },
    },
    {
      id: "row-8",
      rowId: 8,
      text: "Check #16",
      truth: "TRANSACTION_INFO",
      baseline: "TRANSACTION_INFO",
      hybrid: "TRANSACTION_INFO",
      sourceTruth: "TRANSACTION_INFO",
      sourceBaseline: "TRANSACTION_INFO",
      sourceHybrid: "TRANSACTION_INFO",
      bbox: { xMin: 0.08436126395558124, xMax: 0.2872399413237453, yMin: 0.6158350615110841, yMax: 0.6414323816642471 },
    },
    {
      id: "row-10",
      rowId: 10,
      text: "1 Kid Scoop  $5.25",
      truth: "ITEMS",
      baseline: "ITEMS",
      hybrid: "ITEMS",
      sourceTruth: "ITEMS",
      sourceBaseline: "ITEMS",
      sourceHybrid: "ITEMS",
      bbox: { xMin: 0.0859641899893249, xMax: 0.9849961475574337, yMin: 0.5144183078305706, yMax: 0.5547417460194478 },
    },
    {
      id: "row-11",
      rowId: 11,
      text: "Subtotal",
      truth: "SUMMARY",
      baseline: "TRANSACTION_INFO",
      hybrid: "SUMMARY",
      sourceTruth: "SUMMARY",
      sourceBaseline: "SECTION_HEADER",
      sourceHybrid: "SUMMARY",
      bbox: { xMin: 0.0845921503955299, xMax: 0.25981872815431895, yMin: 0.47383720902711646, yMax: 0.49709302306590664 },
    },
    {
      id: "row-12",
      rowId: 12,
      text: "Tax  $5.25",
      truth: "SUMMARY",
      baseline: "ITEMS",
      hybrid: "SUMMARY",
      sourceTruth: "SUMMARY",
      sourceBaseline: "ITEMS",
      sourceHybrid: "SUMMARY",
      bbox: { xMin: 0.07854984832382587, xMax: 0.9791741579031199, yMin: 0.4491279073382469, yMax: 0.48293016961824553 },
    },
    {
      id: "row-29",
      rowId: 29,
      text: "$0.44",
      truth: "SUMMARY",
      baseline: "ITEMS",
      hybrid: "SUMMARY",
      sourceTruth: "SUMMARY",
      sourceBaseline: "ITEMS",
      sourceHybrid: "SUMMARY",
      bbox: { xMin: 0.864048341546288, xMax: 0.9788519609310535, yMin: 0.4316860462364188, yMax: 0.4549418602752091 },
    },
    {
      id: "row-30",
      rowId: 30,
      text: "$0.79",
      truth: "SUMMARY",
      baseline: "ITEMS",
      hybrid: "SUMMARY",
      sourceTruth: "SUMMARY",
      sourceBaseline: "ITEMS",
      sourceHybrid: "SUMMARY",
      bbox: { xMin: 0.8640483400604525, xMax: 0.9818731081268587, yMin: 0.40406976735075995, yMax: 0.4273255813895501 },
    },
    {
      id: "row-14",
      rowId: 14,
      text: "Total",
      truth: "SUMMARY",
      baseline: "TRANSACTION_INFO",
      hybrid: "SUMMARY",
      sourceTruth: "TOTAL_LINE",
      sourceBaseline: "SECTION_HEADER",
      sourceHybrid: "TOTAL_LINE",
      bbox: { xMin: 0.06948640334129311, xMax: 0.18126888167381264, yMin: 0.3909883722829304, yMax: 0.41424418632172066 },
    },
    {
      id: "row-31",
      rowId: 31,
      text: "$6.48",
      truth: "SUMMARY",
      baseline: "ITEMS",
      hybrid: "PAYMENT",
      sourceTruth: "TOTAL_LINE",
      sourceBaseline: "ITEMS",
      sourceHybrid: "PAYMENT",
      bbox: { xMin: 0.86393578019384, xMax: 0.9819856688046797, yMin: 0.37487560840177114, yMax: 0.39983369441556516 },
    },
    {
      id: "row-15",
      rowId: 15,
      text: "Input Type  C (EMV Chip Read)",
      truth: "PAYMENT",
      baseline: "PAYMENT",
      hybrid: "PAYMENT",
      sourceTruth: "PAYMENT",
      sourceBaseline: "PAYMENT",
      sourceHybrid: "PAYMENT",
      bbox: { xMin: 0.06042296107288371, xMax: 0.9833943142488845, yMin: 0.31496484453490325, yMax: 0.3548076919394373 },
    },
    {
      id: "row-16",
      rowId: 16,
      text: "VISA DEBIT  •••• ••••",
      truth: "PAYMENT",
      baseline: "PAYMENT",
      hybrid: "PAYMENT",
      sourceTruth: "PAYMENT",
      sourceBaseline: "PAYMENT",
      sourceHybrid: "PAYMENT",
      bbox: { xMin: 0.05431972610547643, xMax: 0.2810277034003983, yMin: 0.3021877615959917, yMax: 0.32571921502205936 },
    },
    {
      id: "row-34",
      rowId: 34,
      text: "12:54 PM",
      truth: "PAYMENT",
      baseline: "TRANSACTION_INFO",
      hybrid: "PAYMENT",
      sourceTruth: "PAYMENT",
      sourceBaseline: "TRANSACTION_INFO",
      sourceHybrid: "PAYMENT",
      bbox: { xMin: 0.8002513412598269, xMax: 0.9852471450928346, yMin: 0.2580989826297774, yMax: 0.28405218010902544 },
    },
    {
      id: "row-18",
      rowId: 18,
      text: "Transaction Type  Sale",
      truth: "PAYMENT",
      baseline: "PAYMENT",
      hybrid: "PAYMENT",
      sourceTruth: "PAYMENT",
      sourceBaseline: "PAYMENT",
      sourceHybrid: "PAYMENT",
      bbox: { xMin: 0.04061723786687492, xMax: 0.9879154062440662, yMin: 0.22819767460851192, yMax: 0.26802326052058534 },
    },
    {
      id: "row-36",
      rowId: 36,
      text: "Approved",
      truth: "PAYMENT",
      baseline: "PAYMENT",
      hybrid: "PAYMENT",
      sourceTruth: "PAYMENT",
      sourceBaseline: "PAYMENT",
      sourceHybrid: "PAYMENT",
      bbox: { xMin: 0.7974159531303394, xMax: 0.9880825333305348, yMin: 0.1959203958698077, yMax: 0.22123076734209302 },
    },
  ],
};

export const RECEIPT_ROWS = CURRENT_RECEIPT.rows;

export const CHANGED_ROW_IDS = new Set(
  RECEIPT_ROWS.filter((row) => row.baseline !== row.hybrid).map((row) => row.id),
);

export const CORRECTED_ROW_IDS = new Set(
  RECEIPT_ROWS.filter(
    (row) => row.baseline !== row.hybrid && row.hybrid === row.truth,
  ).map((row) => row.id),
);

export const UNRESOLVED_ROW_IDS = new Set(
  RECEIPT_ROWS.filter(
    (row) => row.baseline !== row.hybrid && row.hybrid !== row.truth,
  ).map((row) => row.id),
);

export interface ReferenceReceiptRow {
  id: string;
  text: string;
  section: SectionId;
  matches?: boolean;
  similarity?: number;
  bbox: BoundingBox;
}

export interface ReferenceReceipt {
  id: string;
  sourceId: string;
  merchant: string;
  bestSimilarity: number;
  neighborSection: "SUMMARY" | "PAYMENT";
  imageId: string;
  imageKey: string;
  width: number;
  height: number;
  rows: ReferenceReceiptRow[];
}

/** Real QA-valid neighbors returned by the frozen train + validation corpus. */
export const REFERENCE_RECEIPTS: ReferenceReceipt[] = [
  {
    id: "05de0b2d",
    sourceId: "05de0b2d · R1",
    merchant: "Brooklyn's Best Pizza & Pasta",
    bestSimilarity: 0.9006,
    neighborSection: "SUMMARY",
    imageId: "05de0b2d-dbec-4531-87df-ded7b7a838ae",
    imageKey: "assets/05de0b2d-dbec-4531-87df-ded7b7a838ae/1.webp",
    width: 298,
    height: 856,
    rows: [
      { id: "05de0b2d-13", text: "Check #233", section: "TRANSACTION_INFO", bbox: { xMin: 0.02461650716763117, xMax: 0.9921757418553313, yMin: 0.5111637776211981, yMax: 0.5353858475803746 } },
      { id: "05de0b2d-14", text: "Ordered: 5/24/26 8:17 PM", section: "TRANSACTION_INFO", bbox: { xMin: 0.020683214131498488, xMax: 0.9958333272401257, yMin: 0.48695652206284357, yMax: 0.5132299470962375 } },
      { id: "05de0b2d-15", text: "1 Specialty Slice  $5.00", section: "ITEMS", bbox: { xMin: 0.020833344547526025, xMax: 0.9875000002083333, yMin: 0.44347826057143036, yMax: 0.46521739122759087 } },
      { id: "05de0b2d-18", text: "Tax  $0.42", section: "SUMMARY", matches: true, similarity: 0.9006, bbox: { xMin: 0.02083333401462132, xMax: 0.9796297266114673, yMin: 0.35615673332073483, yMax: 0.3811594205585088 } },
      { id: "05de0b2d-20", text: "Input Type  C (EMV Chip Read)", section: "PAYMENT", bbox: { xMin: 0.016524995121292477, xMax: 0.9708333346354165, yMin: 0.2913043481430494, yMax: 0.311812660101507 } },
      { id: "05de0b2d-21", text: "VISA DEBIT  •••• ••••", section: "PAYMENT", bbox: { xMin: 0.016666667854166633, xMax: 0.9708333313690476, yMin: 0.26949860692742866, yMax: 0.28840579729280824 } },
      { id: "05de0b2d-22", text: "Transaction Type  Sale", section: "PAYMENT", bbox: { xMin: 0.01249999531250009, xMax: 0.9791666671666667, yMin: 0.24637681146976464, yMax: 0.2652173914667213 } },
    ],
  },
  {
    id: "534f5ac3",
    sourceId: "534f5ac3 · R1",
    merchant: "Mouthful Eatery",
    bestSimilarity: 0.8469,
    neighborSection: "PAYMENT",
    imageId: "534f5ac3-528e-44a5-81c3-b2164691ed34",
    imageKey: "assets/534f5ac3-528e-44a5-81c3-b2164691ed34/1.webp",
    width: 844,
    height: 1845,
    rows: [
      { id: "534f5ac3-16", text: "Transaction Type  Sale", section: "PAYMENT", bbox: { xMin: 0.012698413113343292, xMax: 0.9873015839877272, yMin: 0.5232558142112325, yMax: 0.5466605673212334 } },
      { id: "534f5ac3-17", text: "Authorization  Approved", section: "PAYMENT", matches: true, similarity: 0.7025, bbox: { xMin: 0.0158730123339586, xMax: 0.987684683877715, yMin: 0.4992901073900855, yMax: 0.5210587296881212 } },
      { id: "534f5ac3-18", text: "Approval Code  ••••••", section: "PAYMENT", matches: true, similarity: 0.7285, bbox: { xMin: 0.015748855640673496, xMax: 0.9876722120071473, yMin: 0.47495325981162717, yMax: 0.4990656500320111 } },
      { id: "534f5ac3-19", text: "Payment ID  ••••••••", section: "PAYMENT", matches: true, similarity: 0.8469, bbox: { xMin: 0.015517874210284617, xMax: 0.9874357500199271, yMin: 0.4512162712567863, yMax: 0.47560860176019104 } },
      { id: "534f5ac3-20", text: "Application ID  ••••••••", section: "PAYMENT", bbox: { xMin: 0.015873009116963856, xMax: 0.9873015812076668, yMin: 0.42863677987968285, yMax: 0.4505946938778531 } },
      { id: "534f5ac3-21", text: "Application Label  VISA DEBIT", section: "PAYMENT", bbox: { xMin: 0.015873016921026904, xMax: 0.9875559122092072, yMin: 0.40530649556672027, yMax: 0.4279050098228866 } },
      { id: "534f5ac3-22", text: "Card Reader  BBPOS", section: "PAYMENT", bbox: { xMin: 0.015729814639077404, xMax: 0.9904761891149071, yMin: 0.38191141987538724, yMax: 0.40406976762306457 } },
    ],
  },
  {
    id: "72369a3a",
    sourceId: "72369a3a · R1",
    merchant: "Aloha Sunrise Cafe",
    bestSimilarity: 0.8591,
    neighborSection: "SUMMARY",
    imageId: "72369a3a-3f70-4c6d-99f9-0813e509bca4",
    imageKey: "assets/72369a3a-3f70-4c6d-99f9-0813e509bca4/1.webp",
    width: 338,
    height: 718,
    rows: [
      { id: "72369a3a-12", text: "1 Jarritos Soda  $3.49", section: "ITEMS", bbox: { xMin: 0.018518518855436355, xMax: 0.9938271583147716, yMin: 0.6584302328349758, yMax: 0.6816860463925929 } },
      { id: "72369a3a-13", text: "1 Spam Musubi  $3.69", section: "ITEMS", bbox: { xMin: 0.015432098493373545, xMax: 0.9939370584702212, yMin: 0.6337099808316321, yMax: 0.6600168536229825 } },
      { id: "72369a3a-16", text: "Subtotal  $41.06", section: "SUMMARY", bbox: { xMin: -0.0003661445969677146, xMax: 0.9908209205877049, yMin: 0.5870861214208098, yMax: 0.6112439677483434 } },
      { id: "72369a3a-17", text: "Tax 8.375%  $3.44", section: "SUMMARY", matches: true, similarity: 0.8591, bbox: { xMin: 0.0030864197304808273, xMax: 0.9814814805917245, yMin: 0.56104651172648, yMax: 0.5857558141106162 } },
      { id: "72369a3a-18", text: "Total  $44.50", section: "SUMMARY", bbox: { xMin: 0.012345681040883008, xMax: 0.9816773426980626, yMin: 0.5389329910334377, yMax: 0.5613577068693826 } },
      { id: "72369a3a-20", text: "DEBIT CARD SALE  $44.50", section: "PAYMENT", bbox: { xMin: 0.018518513873617564, xMax: 0.975308643670665, yMin: 0.504360465209359, yMax: 0.5263653484222194 } },
      { id: "72369a3a-21", text: "VISA  ••••", section: "PAYMENT", bbox: { xMin: 0.02129613636958686, xMax: 0.2441359648607978, yMin: 0.48317074597462584, yMax: 0.5037478588082847 } },
    ],
  },
  {
    id: "2732aa05",
    sourceId: "2732aa05 · R1",
    merchant: "Jamba",
    bestSimilarity: 0.8402,
    neighborSection: "PAYMENT",
    imageId: "2732aa05-e670-4bab-a6a3-4b841c24f431",
    imageKey: "assets/2732aa05-e670-4bab-a6a3-4b841c24f431/1.webp",
    width: 857,
    height: 2522,
    rows: [
      { id: "2732aa05-20", text: "Transaction Type  Sale", section: "PAYMENT", matches: true, similarity: 0.8209, bbox: { xMin: 0.020833335364583292, xMax: 0.9791666656666667, yMin: 0.42008486598481276, yMax: 0.43847241817360316 } },
      { id: "2732aa05-21", text: "Authorization  Approved", section: "PAYMENT", matches: true, similarity: 0.6895, bbox: { xMin: 0.020833331041666726, xMax: 0.9797946965191265, yMin: 0.402397686390478, yMax: 0.42221334643178954 } },
      { id: "2732aa05-22", text: "Approval Code  ••••••", section: "PAYMENT", matches: true, similarity: 0.7119, bbox: { xMin: 0.02063123263451695, xMax: 0.9792930998462025, yMin: 0.38571579923219124, yMax: 0.40463567440492054 } },
      { id: "2732aa05-23", text: "Payment ID  ••••••••", section: "PAYMENT", matches: true, similarity: 0.8402, bbox: { xMin: 0.020670088504964143, xMax: 0.9791666687916666, yMin: 0.36916548858735865, yMax: 0.38755304077614905 } },
      { id: "2732aa05-24", text: "Application ID  ••••••••", section: "PAYMENT", bbox: { xMin: 0.02083334084960944, xMax: 0.9791666685416666, yMin: 0.3521923618108965, yMax: 0.3691654882029577 } },
      { id: "2732aa05-25", text: "Application Label  VISA DEBIT", section: "PAYMENT", bbox: { xMin: 0.020833335711263088, xMax: 0.9751302883585702, yMin: 0.3350254885145023, yMax: 0.35238610968695117 } },
      { id: "2732aa05-26", text: "Card Reader  BBPOS", section: "PAYMENT", bbox: { xMin: 0.02071924490834209, xMax: 0.9710535601208902, yMin: 0.31804939029374213, yMax: 0.3340250224603445 } },
    ],
  },
];

export type ExplorerActId =
  | "ocr"
  | "baseline"
  | "neighbors"
  | "corrected"
  | "final";

export interface ExplorerAct {
  id: ExplorerActId;
  index: number;
  label: string;
  accessibleLabel: string;
  dwellMs: number;
}

export const EXPLORER_ACTS: ExplorerAct[] = [
  {
    id: "ocr",
    index: 0,
    label: "Real OCR rows",
    accessibleLabel: "Step 1: show real Apple OCR rows before section assignment",
    dwellMs: 5200,
  },
  {
    id: "baseline",
    index: 1,
    label: "Baseline sections",
    accessibleLabel: "Step 2: show measured baseline assignments and incorrect boundaries",
    dwellMs: 6800,
  },
  {
    id: "neighbors",
    index: 2,
    label: "Real Chroma neighbors",
    accessibleLabel: "Step 3: let real Chroma neighbors vote across QA-valid receipts",
    dwellMs: 7600,
  },
  {
    id: "corrected",
    index: 3,
    label: "Move boundaries",
    accessibleLabel: "Step 4: apply the measured hybrid assignments downstream",
    dwellMs: 7200,
  },
  {
    id: "final",
    index: 4,
    label: "Contiguous result",
    accessibleLabel: "Step 5: show contiguous hybrid bands, six fixes, and one unresolved row",
    dwellMs: 8200,
  },
];

export const EXPERIMENT_METRICS = {
  receipts: 167,
  rows: 4214,
  baselineAgreement: 85.95,
  hybridAgreement: 90.84,
  deltaPoints: 4.89,
  fixed: 236,
  regressed: 30,
} as const;
