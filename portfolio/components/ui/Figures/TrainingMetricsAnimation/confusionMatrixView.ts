// Normalize ADDRESS_LINE to ADDRESS for display purposes
export const normalizeLabel = (label: string): string => {
  if (label === "ADDRESS_LINE") return "ADDRESS";
  return label;
};

// Format label: "MERCHANT_NAME" -> "Merchant Name", "O" -> "None"
export const formatLabel = (label: string): string => {
  const normalized = normalizeLabel(label);
  if (normalized === "O") return "None";
  return normalized
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
};

// Abbreviated labels for confusion matrix (drill-in member labels)
export const LABEL_ABBREV: Record<string, string> = {
  MERCHANT_NAME: "Merch",
  DATE: "Date",
  TIME: "Time",
  AMOUNT: "Amt",
  GRAND_TOTAL: "Total",
  SUBTOTAL: "Subt",
  TAX: "Tax",
  LINE_TOTAL: "Line",
  UNIT_PRICE: "Unit",
  DISCOUNT: "Disc",
  COUPON: "Coup",
  TIP: "Tip",
  CHANGE: "Chng",
  CASH_BACK: "Cash",
  REFUND: "Rfnd",
  QUANTITY: "Qty",
  ADDRESS: "Addr",
  ADDRESS_LINE: "Addr",
  PHONE_NUMBER: "Phone",
  WEBSITE: "Web",
  STORE_HOURS: "Hours",
  PAYMENT_METHOD: "Pay",
  O: "O",
};

// Confusion-matrix families — collapse the granular labels into ~10 groups so
// the matrix is readable. Multi-member families (Charges, Credits, Date/Time,
// Hours/Pay, Address) can be drilled into to see their sub-matrix.
export const CM_FAMILIES: { key: string; abbr: string; full: string; members: string[] }[] = [
  { key: "MERCHANT", abbr: "Merch", full: "Merchant", members: ["MERCHANT_NAME"] },
  { key: "DATETIME", abbr: "D/T", full: "Date / Time", members: ["DATE", "TIME"] },
  { key: "CHARGES", abbr: "Chrg", full: "Charges (total·subtotal·tax·line·unit)", members: ["GRAND_TOTAL", "SUBTOTAL", "TAX", "LINE_TOTAL", "UNIT_PRICE", "AMOUNT"] },
  { key: "CREDITS", abbr: "Crdt", full: "Credits (discount·tip·change…)", members: ["DISCOUNT", "COUPON", "TIP", "CHANGE", "CASH_BACK", "REFUND"] },
  { key: "QTY", abbr: "Qty", full: "Quantity", members: ["QUANTITY"] },
  { key: "ADDRESS", abbr: "Addr", full: "Address", members: ["ADDRESS_LINE", "ADDRESS"] },
  { key: "PHONE", abbr: "Phone", full: "Phone", members: ["PHONE_NUMBER"] },
  { key: "WEBSITE", abbr: "Web", full: "Website", members: ["WEBSITE"] },
  { key: "HOURSPAY", abbr: "Hr/Py", full: "Hours / Payment", members: ["STORE_HOURS", "PAYMENT_METHOD"] },
  { key: "O", abbr: "O", full: "O (no label)", members: ["O"] },
];

export interface MatrixView {
  labels: string[];
  titles: string[];
  matrix: number[][];
  drillKeys: (string | null)[];
}

export const buildFamilyView = (labels: string[], matrix: number[][]): MatrixView => {
  const labelToFam = new Map<string, number>();
  CM_FAMILIES.forEach((f, fi) => f.members.forEach((m) => labelToFam.set(m, fi)));
  const present = CM_FAMILIES.map((_f, fi) => fi).filter((fi) =>
    CM_FAMILIES[fi].members.some((m) => labels.includes(m))
  );
  const compact = new Map<number, number>();
  present.forEach((fi, k) => compact.set(fi, k));
  const n = present.length;
  const out = Array.from({ length: n }, () => Array(n).fill(0));
  labels.forEach((rl, i) => {
    const rf = labelToFam.get(rl);
    if (rf === undefined || !compact.has(rf)) return;
    labels.forEach((cl, j) => {
      const cf = labelToFam.get(cl);
      if (cf === undefined || !compact.has(cf)) return;
      out[compact.get(rf) as number][compact.get(cf) as number] +=
        matrix[i]?.[j] || 0;
    });
  });
  return {
    labels: present.map((fi) => CM_FAMILIES[fi].abbr),
    titles: present.map((fi) => CM_FAMILIES[fi].full),
    matrix: out,
    drillKeys: present.map((fi) =>
      CM_FAMILIES[fi].members.filter((m) => labels.includes(m)).length > 1
        ? CM_FAMILIES[fi].key
        : null
    ),
  };
};

export const buildSubView = (
  labels: string[],
  matrix: number[][],
  famKey: string
): MatrixView | null => {
  const fam = CM_FAMILIES.find((f) => f.key === famKey);
  if (!fam) return null;
  const idxs = fam.members
    .map((m) => labels.indexOf(m))
    .filter((x) => x >= 0);
  if (idxs.length < 2) return null;
  return {
    labels: idxs.map((x) => formatLabelAbbrev(labels[x])),
    titles: idxs.map((x) => formatLabel(labels[x])),
    matrix: idxs.map((ri) => idxs.map((ci) => matrix[ri]?.[ci] || 0)),
    drillKeys: idxs.map(() => null),
  };
};

export const formatLabelAbbrev = (label: string): string => {
  const normalized = normalizeLabel(label);
  return LABEL_ABBREV[normalized] || normalized.slice(0, 4);
};
