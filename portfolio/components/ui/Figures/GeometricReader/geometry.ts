import {
  DecodedReceiptLineItem,
  LineItemDecodeLine,
  LineItemDecodeReceipt,
  LineItemDecodeSection,
  LineItemReconciliationStatus,
} from "../../../../types/api";

export interface NormalizedBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ReceiptProof {
  status: LineItemReconciliationStatus;
  decodedTotal: number;
  printedSubtotal: number | null;
  delta: number | null;
}

const roundCurrency = (value: number): number => Math.round(value * 100) / 100;

export const boundsForLineIds = (
  lines: LineItemDecodeLine[],
  lineIds: number[],
): NormalizedBounds | null => {
  const wanted = new Set(lineIds);
  const boxes = lines
    .filter((line) => wanted.has(line.line_id))
    .map((line) => line.bounding_box);
  if (boxes.length === 0) return null;

  let left = 1;
  let top = 1;
  let right = 0;
  let bottom = 0;
  for (const box of boxes) {
    left = Math.min(left, box.x);
    top = Math.min(top, 1 - box.y - box.height);
    right = Math.max(right, box.x + box.width);
    bottom = Math.max(bottom, 1 - box.y);
  }

  return {
    x: Math.max(0, left),
    y: Math.max(0, top),
    width: Math.min(1, right) - Math.max(0, left),
    height: Math.min(1, bottom) - Math.max(0, top),
  };
};

export const orderSections = (
  sections: LineItemDecodeSection[],
): LineItemDecodeSection[] =>
  [...sections].sort((left, right) => {
    const leftLine = Math.min(...left.line_ids);
    const rightLine = Math.min(...right.line_ids);
    return leftLine - rightLine;
  });

const storedReceiptStatus = (
  lineItems: DecodedReceiptLineItem[],
): LineItemReconciliationStatus => {
  const statuses = new Set(
    lineItems.map((item) => item.reconciliation_status).filter(Boolean),
  );
  if (statuses.has("mismatch")) return "mismatch";
  if (statuses.has("near")) return "near";
  if (statuses.has("match") && statuses.size === 1) return "match";
  return "no-baseline";
};

export const getReceiptProof = (
  receipt: Pick<LineItemDecodeReceipt, "line_items" | "printed_subtotal">,
): ReceiptProof => {
  const decodedTotal = roundCurrency(
    receipt.line_items.reduce((sum, item) => {
      const value = Number.parseFloat(item.price);
      return Number.isFinite(value) ? sum + value : sum;
    }, 0),
  );
  const printedSubtotal = receipt.printed_subtotal;
  if (printedSubtotal === null) {
    return {
      status: "no-baseline",
      decodedTotal,
      printedSubtotal,
      delta: null,
    };
  }

  const delta = roundCurrency(decodedTotal - printedSubtotal);
  const storedStatus = storedReceiptStatus(receipt.line_items);
  const status =
    storedStatus === "match" && Math.abs(delta) >= 0.005
      ? "mismatch"
      : storedStatus;

  return { status, decodedTotal, printedSubtotal, delta };
};

export const findPriceCarrierLineId = (
  item: DecodedReceiptLineItem,
  lines: LineItemDecodeLine[],
): number | null => {
  const price = Math.abs(Number.parseFloat(item.price)).toFixed(2);
  const candidates = lines.filter((line) =>
    item.line_ids.includes(line.line_id),
  );
  return (
    [...candidates]
      .reverse()
      .find((line) => line.text.replaceAll(",", "").includes(price))?.line_id ??
    item.line_ids.at(-1) ??
    null
  );
};
