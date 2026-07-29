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
    shortLabel: "SUM",
    label: "Summary",
    color: "var(--color-orange)",
  },
  {
    id: "PAYMENT",
    shortLabel: "PAY",
    label: "Payment",
    color: "var(--color-green)",
  },
];

export const SECTION_BY_ID = Object.fromEntries(
  SECTIONS.map((section) => [section.id, section]),
) as Record<SectionId, SectionDefinition>;

export interface ReceiptRow {
  id: string;
  text: string;
  amount?: string;
  truth: SectionId;
  baseline: SectionId;
}

/**
 * A compact, intentionally schematic receipt. The row text makes the evidence
 * legible; the held-out metrics shown by the figure are the measured values.
 */
export const RECEIPT_ROWS: ReceiptRow[] = [
  {
    id: "merchant",
    text: "SPROUTS FARMERS MARKET",
    truth: "TRANSACTION_INFO",
    baseline: "TRANSACTION_INFO",
  },
  {
    id: "address",
    text: "1234 MARKET STREET",
    truth: "TRANSACTION_INFO",
    baseline: "TRANSACTION_INFO",
  },
  {
    id: "date",
    text: "07/29/26  12:41 PM",
    truth: "TRANSACTION_INFO",
    baseline: "TRANSACTION_INFO",
  },
  {
    id: "milk",
    text: "ORGANIC WHOLE MILK",
    amount: "4.99",
    truth: "ITEMS",
    baseline: "ITEMS",
  },
  {
    id: "bananas",
    text: "BANANAS 1.34 LB",
    amount: "1.58",
    truth: "ITEMS",
    baseline: "ITEMS",
  },
  {
    id: "avocado",
    text: "HASS AVOCADO",
    amount: "2.50",
    truth: "ITEMS",
    baseline: "ITEMS",
  },
  {
    id: "subtotal",
    text: "SUBTOTAL",
    amount: "9.07",
    truth: "SUMMARY",
    baseline: "ITEMS",
  },
  {
    id: "tax",
    text: "TAX",
    amount: "0.00",
    truth: "SUMMARY",
    baseline: "SUMMARY",
  },
  {
    id: "total",
    text: "TOTAL",
    amount: "9.07",
    truth: "SUMMARY",
    baseline: "SUMMARY",
  },
  {
    id: "visa",
    text: "VISA ******** 1234",
    truth: "PAYMENT",
    baseline: "SUMMARY",
  },
  {
    id: "approved",
    text: "APPROVED",
    truth: "PAYMENT",
    baseline: "PAYMENT",
  },
];

export interface QueryScenario {
  id: "subtotal" | "visa" | "milk";
  label: string;
  rowId: string;
  x: number;
  y: number;
  votes: Record<SectionId, number>;
  baseline: SectionId;
  decoded: SectionId;
}

export const QUERY_SCENARIOS: QueryScenario[] = [
  {
    id: "subtotal",
    label: "SUBTOTAL 9.07",
    rowId: "subtotal",
    x: 64,
    y: 57,
    votes: {
      TRANSACTION_INFO: 0.03,
      ITEMS: 0.16,
      SUMMARY: 0.76,
      PAYMENT: 0.05,
    },
    baseline: "ITEMS",
    decoded: "SUMMARY",
  },
  {
    id: "visa",
    label: "VISA •••• 1234",
    rowId: "visa",
    x: 78,
    y: 78,
    votes: {
      TRANSACTION_INFO: 0.02,
      ITEMS: 0.03,
      SUMMARY: 0.1,
      PAYMENT: 0.85,
    },
    baseline: "SUMMARY",
    decoded: "PAYMENT",
  },
  {
    id: "milk",
    label: "WHOLE MILK 4.99",
    rowId: "milk",
    x: 30,
    y: 48,
    votes: {
      TRANSACTION_INFO: 0.02,
      ITEMS: 0.92,
      SUMMARY: 0.05,
      PAYMENT: 0.01,
    },
    baseline: "ITEMS",
    decoded: "ITEMS",
  },
];

export const QUERY_BY_ID = Object.fromEntries(
  QUERY_SCENARIOS.map((scenario) => [scenario.id, scenario]),
) as Record<QueryScenario["id"], QueryScenario>;

export interface ProjectionPoint {
  id: string;
  merchant: string;
  section: SectionId;
  x: number;
  y: number;
}

const CLUSTER_CENTERS: Record<SectionId, { x: number; y: number }> = {
  TRANSACTION_INFO: { x: 23, y: 23 },
  ITEMS: { x: 31, y: 52 },
  SUMMARY: { x: 64, y: 55 },
  PAYMENT: { x: 78, y: 79 },
};

const POINT_OFFSETS = [
  [-8, -2],
  [-5, 6],
  [-2, -7],
  [1, 3],
  [4, -4],
  [6, 5],
  [9, 0],
  [0, 9],
] as const;

const MERCHANTS = [
  "Costco",
  "Vons",
  "Target",
  "CVS",
  "Trader Joe's",
  "In-N-Out",
  "Wild Fork",
  "Ralphs",
];

export const PROJECTION_POINTS: ProjectionPoint[] = SECTIONS.flatMap(
  (section) => {
    const center = CLUSTER_CENTERS[section.id];
    return POINT_OFFSETS.map(([dx, dy], index) => ({
      id: `${section.id}-${index}`,
      merchant: MERCHANTS[index],
      section: section.id,
      x: center.x + dx,
      y: center.y + dy,
    }));
  },
);

/** Return the 15 visible cross-receipt neighbors closest in the 2-D explainer. */
export const nearestProjectionPoints = (
  scenario: QueryScenario,
  count = 15,
): ProjectionPoint[] =>
  PROJECTION_POINTS.map((point) => ({
    point,
    distance: Math.hypot(point.x - scenario.x, point.y - scenario.y),
  }))
    .sort((a, b) => a.distance - b.distance)
    .slice(0, count)
    .map(({ point }) => point);

export type ExplorerActId =
  | "receipt"
  | "projection"
  | "neighbors"
  | "decode"
  | "result";

export interface ExplorerAct {
  id: ExplorerActId;
  index: number;
  label: string;
  accessibleLabel: string;
  dwellMs: number;
}

export const EXPLORER_ACTS: ExplorerAct[] = [
  {
    id: "receipt",
    index: 0,
    label: "Read the rows",
    accessibleLabel: "Step 1: read the receipt as an ordered row sequence",
    dwellMs: 5200,
  },
  {
    id: "projection",
    index: 1,
    label: "Project meaning",
    accessibleLabel: "Step 2: project rows into embedding space",
    dwellMs: 7000,
  },
  {
    id: "neighbors",
    index: 2,
    label: "Let neighbors vote",
    accessibleLabel: "Step 3: collect cosine-weighted cross-receipt votes",
    dwellMs: 8000,
  },
  {
    id: "decode",
    index: 3,
    label: "Decode the sequence",
    accessibleLabel: "Step 4: decode one contiguous receipt sequence",
    dwellMs: 7600,
  },
  {
    id: "result",
    index: 4,
    label: "Check the holdout",
    accessibleLabel: "Step 5: compare held-out accuracy",
    dwellMs: 9000,
  },
];

export const EXPERIMENT_METRICS = {
  receipts: 167,
  rows: 4214,
  baselineCorrect: 3622,
  hybridCorrect: 3828,
  baselineAgreement: 85.95,
  hybridAgreement: 90.84,
  deltaPoints: 4.89,
  fixed: 236,
  regressed: 30,
  coverage: 99.87,
  bootstrapLow: 3.87,
  bootstrapHigh: 5.94,
} as const;

