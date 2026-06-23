export interface ConfusionPair {
  id: string;
  actualLabel: string;
  predictedLabel: string;
  count: number;
  actualTotal: number;
  share: number;
  patternTarget: string;
  syntheticTarget: string;
  llmCue: string;
}

export type ReceiptHeatmapZone =
  | "header"
  | "identity"
  | "items"
  | "totals"
  | "footer";

export interface PatternHeatmapCell {
  zone: ReceiptHeatmapZone;
  label: string;
  count: number;
  intensity: number;
  pairIds: string[];
  llmPrompt: string;
  syntheticBrief: string;
}

export interface PatternHeatmapPlan {
  cells: PatternHeatmapCell[];
  maxCount: number;
  syntheticReceiptCount: number;
}

const normalizeBaseLabel = (label: string): string => {
  if (label.startsWith("B-") || label.startsWith("I-")) {
    return label.slice(2);
  }
  if (label === "ADDRESS_LINE") return "ADDRESS";
  return label;
};

const isAmountLike = (label: string): boolean =>
  [
    "AMOUNT",
    "GRAND_TOTAL",
    "SUBTOTAL",
    "TAX",
    "LINE_TOTAL",
    "UNIT_PRICE",
    "DISCOUNT",
    "COUPON",
    "TIP",
    "CHANGE",
    "CASH_BACK",
    "REFUND",
  ].includes(label);

const isDateTimeLike = (label: string): boolean =>
  ["DATE", "TIME", "STORE_HOURS"].includes(label);

const ZONE_LABELS: Record<ReceiptHeatmapZone, string> = {
  header: "Header",
  identity: "Identity",
  items: "Items",
  totals: "Totals",
  footer: "Footer",
};

const LABEL_ZONES: Record<string, ReceiptHeatmapZone[]> = {
  MERCHANT_NAME: ["header", "identity"],
  ADDRESS: ["identity"],
  DATE: ["header"],
  TIME: ["header"],
  STORE_HOURS: ["footer"],
  PHONE_NUMBER: ["footer"],
  WEBSITE: ["footer"],
  PAYMENT_METHOD: ["footer", "totals"],
  QUANTITY: ["items"],
  LINE_TOTAL: ["items", "totals"],
  UNIT_PRICE: ["items"],
  AMOUNT: ["items", "totals"],
  SUBTOTAL: ["totals"],
  TAX: ["totals"],
  GRAND_TOTAL: ["totals"],
  DISCOUNT: ["items", "totals"],
  COUPON: ["items", "totals"],
  TIP: ["totals"],
  CHANGE: ["totals"],
  CASH_BACK: ["totals"],
  REFUND: ["totals"],
  O: ["items"],
};

const pairDomain = (actualLabel: string, predictedLabel: string): string => {
  const labels = [
    normalizeBaseLabel(actualLabel),
    normalizeBaseLabel(predictedLabel),
  ];
  if (labels.includes("MERCHANT_NAME")) return "merchant header";
  if (labels.includes("ADDRESS")) return "address block";
  if (labels.some(isAmountLike)) return "price row";
  if (labels.some(isDateTimeLike)) return "date/time block";
  if (labels.includes("PHONE_NUMBER") || labels.includes("WEBSITE")) {
    return "contact footer";
  }
  return "layout neighborhood";
};

export const describeSyntheticTarget = (
  actualLabel: string,
  predictedLabel: string
): Pick<ConfusionPair, "patternTarget" | "syntheticTarget" | "llmCue"> => {
  const actual = normalizeBaseLabel(actualLabel);
  const predicted = normalizeBaseLabel(predictedLabel);
  const domain = pairDomain(actual, predicted);

  if (predicted === "O") {
    return {
      patternTarget: `Missed ${domain}`,
      syntheticTarget: `${actual} examples with varied neighbors`,
      llmCue: `Find where real ${actual} tokens disappear into background text.`,
    };
  }

  if (actual === "O") {
    return {
      patternTarget: `False ${domain}`,
      syntheticTarget: `Background tokens that resemble ${predicted}`,
      llmCue: `Find negative examples near ${predicted} zones before synthesizing.`,
    };
  }

  if (actual === predicted) {
    return {
      patternTarget: domain,
      syntheticTarget: `${actual} stabilizers`,
      llmCue: `Confirm this pair is not a real confusion before mining.`,
    };
  }

  return {
    patternTarget: `${domain} swap`,
    syntheticTarget: `${actual} vs ${predicted} contrast set`,
    llmCue: `Compare receipt neighborhoods where ${actual} and ${predicted} trade places.`,
  };
};

export const buildTopConfusionPairs = (
  labels: string[],
  matrix: number[][],
  limit = 3
): ConfusionPair[] => {
  const pairs: ConfusionPair[] = [];

  labels.forEach((actualLabel, i) => {
    const row = matrix[i] || [];
    const actualTotal = row.reduce((sum, value) => sum + (value || 0), 0);
    if (actualTotal <= 0) return;

    row.forEach((count, j) => {
      if (i === j || !count || count <= 0) return;
      const predictedLabel = labels[j];
      if (!predictedLabel) return;
      const pattern = describeSyntheticTarget(actualLabel, predictedLabel);
      pairs.push({
        id: `${actualLabel}->${predictedLabel}`,
        actualLabel,
        predictedLabel,
        count,
        actualTotal,
        share: count / actualTotal,
        ...pattern,
      });
    });
  });

  return pairs
    .sort((a, b) => b.count - a.count || b.share - a.share)
    .slice(0, Math.max(0, limit));
};

const zonesForPair = (pair: ConfusionPair): ReceiptHeatmapZone[] => {
  const actual = normalizeBaseLabel(pair.actualLabel);
  const predicted = normalizeBaseLabel(pair.predictedLabel);
  const focusLabel = predicted === "O" ? actual : predicted;
  const zones = new Set<ReceiptHeatmapZone>(LABEL_ZONES[focusLabel] || []);

  if (actual !== "O" && predicted !== "O" && actual !== predicted) {
    (LABEL_ZONES[actual] || []).forEach((zone) => zones.add(zone));
  }

  if (zones.size === 0) zones.add("items");
  return Array.from(zones);
};

const zonePrompt = (
  zone: ReceiptHeatmapZone,
  pairs: ConfusionPair[]
): string => {
  const labels = Array.from(
    new Set(
      pairs.flatMap((pair) => [
        normalizeBaseLabel(pair.actualLabel),
        normalizeBaseLabel(pair.predictedLabel),
      ])
    )
  )
    .filter((label) => label !== "O")
    .slice(0, 3);

  if (labels.length === 0) {
    return `Mine ${ZONE_LABELS[zone].toLowerCase()} background tokens that trigger false positives.`;
  }

  return `Ask the LLM for ${ZONE_LABELS[
    zone
  ].toLowerCase()} layouts where ${labels.join(" / ")} changes neighbors, spacing, or wording.`;
};

const syntheticBrief = (
  zone: ReceiptHeatmapZone,
  pairs: ConfusionPair[]
): string => {
  const strongest = pairs[0];
  if (!strongest) return `Hold out real ${ZONE_LABELS[zone].toLowerCase()} receipts.`;

  if (strongest.predictedLabel === "O") {
    return `Add missed ${normalizeBaseLabel(
      strongest.actualLabel
    )} examples in the ${ZONE_LABELS[zone].toLowerCase()}.`;
  }

  if (strongest.actualLabel === "O") {
    return `Add negative ${normalizeBaseLabel(
      strongest.predictedLabel
    )} lookalikes in the ${ZONE_LABELS[zone].toLowerCase()}.`;
  }

  return `Add contrast receipts for ${normalizeBaseLabel(
    strongest.actualLabel
  )} vs ${normalizeBaseLabel(strongest.predictedLabel)}.`;
};

export const buildPatternHeatmapPlan = (
  pairs: ConfusionPair[]
): PatternHeatmapPlan => {
  const buckets = new Map<
    ReceiptHeatmapZone,
    { count: number; pairs: ConfusionPair[] }
  >();

  pairs.forEach((pair) => {
    zonesForPair(pair).forEach((zone) => {
      const bucket = buckets.get(zone) || { count: 0, pairs: [] };
      bucket.count += pair.count;
      bucket.pairs.push(pair);
      buckets.set(zone, bucket);
    });
  });

  const maxCount = Math.max(
    1,
    ...Array.from(buckets.values()).map((bucket) => bucket.count)
  );

  const cells = Array.from(buckets.entries())
    .map(([zone, bucket]) => ({
      zone,
      label: ZONE_LABELS[zone],
      count: bucket.count,
      intensity: Math.max(0.12, Math.min(1, bucket.count / maxCount)),
      pairIds: Array.from(new Set(bucket.pairs.map((pair) => pair.id))),
      llmPrompt: zonePrompt(zone, bucket.pairs),
      syntheticBrief: syntheticBrief(zone, bucket.pairs),
    }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));

  return {
    cells,
    maxCount,
    syntheticReceiptCount: cells.reduce(
      (sum, cell) => sum + Math.max(2, Math.ceil(cell.intensity * 6)),
      0
    ),
  };
};
