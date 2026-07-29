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

export interface ReceiptRow {
  id: string;
  text: string;
  amount?: string;
  truth: SectionId;
  baseline: SectionId;
}

/** A compact held-out receipt with two deliberately wrong baseline boundaries. */
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

export const CHANGED_ROW_IDS = new Set(["subtotal", "visa"]);

export interface ReferenceReceiptRow {
  id: string;
  text: string;
  amount?: string;
  section: SectionId;
  matches?: "subtotal" | "visa";
}

export interface ReferenceReceipt {
  id: string;
  merchant: string;
  rows: ReferenceReceiptRow[];
}

/** Representative labeled receipts. Four of the 15 searched neighbors are drawn. */
export const REFERENCE_RECEIPTS: ReferenceReceipt[] = [
  {
    id: "costco",
    merchant: "COSTCO",
    rows: [
      { id: "costco-info", text: "COSTCO #117", section: "TRANSACTION_INFO" },
      { id: "costco-item", text: "ORG WHOLE MILK", amount: "4.69", section: "ITEMS" },
      { id: "costco-subtotal", text: "SUBTOTAL", amount: "12.47", section: "SUMMARY", matches: "subtotal" },
      { id: "costco-pay", text: "VISA •••• 8472", section: "PAYMENT" },
    ],
  },
  {
    id: "vons",
    merchant: "VONS",
    rows: [
      { id: "vons-info", text: "VONS STORE 2091", section: "TRANSACTION_INFO" },
      { id: "vons-item", text: "BANANAS", amount: "1.92", section: "ITEMS" },
      { id: "vons-summary", text: "TOTAL", amount: "18.39", section: "SUMMARY" },
      { id: "vons-visa", text: "VISA •••• 0064", section: "PAYMENT", matches: "visa" },
    ],
  },
  {
    id: "target",
    merchant: "TARGET",
    rows: [
      { id: "target-info", text: "TARGET T-1329", section: "TRANSACTION_INFO" },
      { id: "target-item", text: "OAT MILK", amount: "5.49", section: "ITEMS" },
      { id: "target-subtotal", text: "SUBTOTAL", amount: "27.18", section: "SUMMARY", matches: "subtotal" },
      { id: "target-pay", text: "CARD APPROVED", section: "PAYMENT" },
    ],
  },
  {
    id: "cvs",
    merchant: "CVS",
    rows: [
      { id: "cvs-info", text: "CVS/PHARMACY", section: "TRANSACTION_INFO" },
      { id: "cvs-item", text: "COUGH DROPS", amount: "6.99", section: "ITEMS" },
      { id: "cvs-summary", text: "TOTAL", amount: "7.61", section: "SUMMARY" },
      { id: "cvs-visa", text: "VISA •••• 4418", section: "PAYMENT", matches: "visa" },
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
    label: "Apple OCR rows",
    accessibleLabel: "Step 1: show Apple OCR rows before section assignment",
    dwellMs: 5200,
  },
  {
    id: "baseline",
    index: 1,
    label: "Baseline sections",
    accessibleLabel: "Step 2: show baseline row sections and incorrect boundaries",
    dwellMs: 6800,
  },
  {
    id: "neighbors",
    index: 2,
    label: "Chroma neighbors",
    accessibleLabel: "Step 3: let Chroma neighbors vote across labeled receipts",
    dwellMs: 7600,
  },
  {
    id: "corrected",
    index: 3,
    label: "Move boundaries",
    accessibleLabel: "Step 4: correct downstream row labels and section boundaries",
    dwellMs: 7200,
  },
  {
    id: "final",
    index: 4,
    label: "Contiguous result",
    accessibleLabel: "Step 5: show contiguous section bands and changed rows",
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
