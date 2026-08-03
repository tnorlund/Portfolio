import { ValidationReceipt, ValidationSummary } from "./types";

export type Agreement = "agree" | "near" | "disagree" | "unknown";

export interface TruthRow {
  key: "items" | "subtotal" | "total" | "bank";
  label: string;
  value: number | null;
  /** What this figure is checked against, one link up the chain. */
  reference: number | null;
  referenceLabel: string;
  agreement: Agreement;
  delta: number | null;
}

export type FailureCode =
  "baseline" | "promo" | "summary-band" | "zone-gap" | "overshoot";

/**
 * The A–J taxonomy from the mismatch survey, offered as reason codes so a
 * verdict can be joined back to the mode it confirms or refutes. There is no
 * E: it was folded into F when the survey was written.
 */
export const FAILURE_MODES: { code: string; label: string }[] = [
  {
    code: "A-total-line-absorbed",
    label: "A · printed total absorbed as an item",
  },
  {
    code: "B-baseline-ocr-broken",
    label: "B · printed baseline itself is wrong",
  },
  {
    code: "C-tender-line-absorbed",
    label: "C · tender line absorbed as an item",
  },
  {
    code: "D-promo-qty-double-count",
    label: "D · promo/quantity band double-counted",
  },
  { code: "F-mixed-junk-bands", label: "F · several junk classes together" },
  { code: "G-phantom-item", label: "G · delta equals one item, no signature" },
  {
    code: "H-zone-gap-missing-items",
    label: "H · ITEMS zone missed real rows",
  },
  { code: "I-digit-fragmentation", label: "I · OCR split a price" },
  { code: "J-unknown", label: "J · no explanation reproduces the delta" },
];

export interface FailureHint {
  code: FailureCode;
  label: string;
  detail: string;
}

const round2 = (value: number): number => Math.round(value * 100) / 100;

// Same tolerance ladder as receipt_upload.line_items.geometry.reconcile, so a
// row painted green here is a row the pipeline itself calls "match".
export const matchTolerance = (reference: number): number =>
  Math.max(0.02, Math.abs(reference) * 0.01);

export const nearTolerance = (reference: number): number =>
  Math.max(1.0, Math.abs(reference) * 0.1);

export const compareAmounts = (
  value: number | null | undefined,
  reference: number | null | undefined,
): { agreement: Agreement; delta: number | null } => {
  if (
    value === null ||
    value === undefined ||
    reference === null ||
    reference === undefined
  ) {
    return { agreement: "unknown", delta: null };
  }
  const delta = round2(value - reference);
  const diff = Math.abs(delta);
  if (diff <= matchTolerance(reference)) return { agreement: "agree", delta };
  if (diff <= nearTolerance(reference)) return { agreement: "near", delta };
  return { agreement: "disagree", delta };
};

/**
 * The four-figure truth chain: Σ items -> printed subtotal -> printed total
 * -> settled bank amount. Each row is scored against the link above it, so a
 * red row names which hop in the chain actually broke.
 */
export const buildTruthChain = (
  itemsSum: number,
  summary: ValidationSummary | null,
): TruthRow[] => {
  const subtotal = summary?.subtotal ?? null;
  const total = summary?.grand_total ?? null;
  const tax = summary?.tax ?? null;
  const bank = summary?.bank_amount ?? null;
  const baseline = summary?.baseline ?? null;
  const expectedTotal =
    subtotal === null ? null : round2(subtotal + (tax ?? 0));

  return [
    {
      key: "items",
      label: "Σ items",
      value: itemsSum,
      reference: baseline,
      referenceLabel: "reconciliation baseline",
      ...compareAmounts(itemsSum, baseline),
    },
    {
      key: "subtotal",
      label: "Printed subtotal",
      value: subtotal,
      reference: itemsSum,
      referenceLabel: "Σ items",
      ...compareAmounts(subtotal, itemsSum),
    },
    {
      key: "total",
      label: "Printed total",
      value: total,
      reference: expectedTotal,
      referenceLabel: "subtotal + tax",
      ...compareAmounts(total, expectedTotal),
    },
    {
      key: "bank",
      label: "Bank amount",
      value: bank,
      reference: total,
      referenceLabel: "printed total",
      ...compareAmounts(bank, total),
    },
  ];
};

/**
 * A golden fixture needs two independent truths to agree: items that
 * reconcile against the printed baseline, and a bank settlement that matches
 * the printed total. Anything less is a claim, not proof.
 */
export const isGoldenReady = (
  receipt: Pick<
    ValidationReceipt,
    "items_sum" | "reconciliation_status" | "summary"
  >,
): boolean =>
  receipt.reconciliation_status === "match" &&
  buildTruthChain(receipt.items_sum, receipt.summary).find(
    (row) => row.key === "bank",
  )?.agreement === "agree";

const nearlyEqual = (left: number, right: number): boolean =>
  Math.abs(left - right) <= matchTolerance(right || left || 1);

/**
 * Name the likely failure mode from the delta's shape. These are hints for the
 * reviewer, not verdicts — the four failure modes seen while labeling the
 * disagreement tail.
 */
export const failureHint = (
  receipt: Pick<ValidationReceipt, "items" | "items_sum" | "delta" | "summary">,
): FailureHint | null => {
  const summary = receipt.summary;
  const baseline = summary?.baseline ?? null;
  if (baseline === null) {
    return {
      code: "baseline",
      label: "No baseline",
      detail:
        "Neither a printed subtotal nor a usable total was extracted, so " +
        "nothing can reconcile. Fix the summary labels first.",
    };
  }

  const delta = receipt.delta;
  if (delta === null) return null;
  if (Math.abs(delta) <= matchTolerance(baseline)) return null;

  const discountTotal = round2(
    receipt.items
      .filter((item) => item.is_discount)
      .reduce((sum, item) => sum + Math.abs(item.price), 0),
  );
  if (delta > 0 && discountTotal > 0 && nearlyEqual(delta, discountTotal)) {
    return {
      code: "promo",
      label: "Promo netting",
      detail:
        `Σ items overshoots by exactly the discount total ` +
        `(${discountTotal.toFixed(2)}). The printed baseline nets promos out; ` +
        "the sum does not.",
    };
  }

  const printedFigures = [
    summary?.grand_total,
    summary?.subtotal,
    summary?.tax,
    summary?.bank_amount,
  ].filter((figure): figure is number => typeof figure === "number");
  // Only a row whose removal would actually close the gap counts as bleed;
  // otherwise every single-item receipt trips this (its item equals the
  // subtotal by definition).
  const bleeding = receipt.items.find(
    (item) =>
      !item.is_discount &&
      nearlyEqual(item.price, delta) &&
      printedFigures.some(
        (figure) => figure > 0 && nearlyEqual(item.price, figure),
      ),
  );
  if (delta > 0 && bleeding) {
    return {
      code: "summary-band",
      label: "Summary band bleed",
      detail:
        `Item "${bleeding.name || "unnamed"}" is priced at ` +
        `${bleeding.price.toFixed(2)}, which equals a printed summary figure. ` +
        "The ITEMS section probably swallowed a summary row.",
    };
  }

  if (delta < 0) {
    return {
      code: "zone-gap",
      label: "Items zone gap",
      detail:
        `Σ items falls ${Math.abs(delta).toFixed(2)} short of the baseline — ` +
        "the ITEMS section is missing rows, or a multi-row block collapsed.",
    };
  }

  return {
    code: "overshoot",
    label: "Unexplained overshoot",
    detail:
      `Σ items exceeds the baseline by ${delta.toFixed(2)} with no matching ` +
      "discount or printed figure. Check for duplicated or misread rows.",
  };
};
