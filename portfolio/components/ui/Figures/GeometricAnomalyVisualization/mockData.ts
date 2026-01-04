/**
 * Mock data for GeometricAnomalyVisualization
 *
 * Represents a real-world scenario: a GRAND_TOTAL label appearing
 * at the wrong position on the receipt, detected by geometric rules.
 */

export interface ReceiptWord {
  id: string;
  text: string;
  x: number; // normalized 0-1 (left to right)
  y: number; // normalized 0-1 (top to bottom, 0 = top)
  width: number;
  height: number;
  label: string | null;
  isFlagged: boolean;
  anomalyType?: string;
  reasoning?: string;
}

export interface LabelPairPattern {
  from: string;
  to: string;
  observations: Array<{ dx: number; dy: number }>;
  mean: { dx: number; dy: number };
  std: number;
}

export interface FlaggedWordInfo {
  wordId: string;
  referenceLabel: string;
  expected: { dx: number; dy: number };
  actual: { dx: number; dy: number };
  zScore: number;
  threshold: number;
}

export interface GeometricAnomalyData {
  receipt: {
    words: ReceiptWord[];
  };
  patterns: {
    labelPairs: LabelPairPattern[];
  };
  flaggedWord: FlaggedWordInfo | null;
}

// Label colors matching the existing palette
export const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  ADDRESS_LINE: "var(--color-purple)",
  PHONE_NUMBER: "var(--color-purple)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  PRODUCT_NAME: "var(--color-gray, #888)",
  LINE_TOTAL: "var(--color-green)",
  SUBTOTAL: "var(--color-green)",
  TAX: "var(--color-red)",
  GRAND_TOTAL: "var(--color-green)",
  unlabeled: "var(--text-color)",
};

// Generate training observations with some variance around mean
function generateObservations(
  meanDx: number,
  meanDy: number,
  std: number,
  count: number
): Array<{ dx: number; dy: number }> {
  const observations = [];
  for (let i = 0; i < count; i++) {
    // Box-Muller transform for normal distribution
    const u1 = Math.random();
    const u2 = Math.random();
    const z0 = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
    const z1 = Math.sqrt(-2 * Math.log(u1)) * Math.sin(2 * Math.PI * u2);

    observations.push({
      dx: meanDx + z0 * std,
      dy: meanDy + z1 * std,
    });
  }
  return observations;
}

export const mockData: GeometricAnomalyData = {
  receipt: {
    words: [
      // Header - Merchant Name
      { id: "w1", text: "ACME", x: 0.35, y: 0.05, width: 0.15, height: 0.03, label: "MERCHANT_NAME", isFlagged: false },
      { id: "w2", text: "GROCERY", x: 0.52, y: 0.05, width: 0.18, height: 0.03, label: "MERCHANT_NAME", isFlagged: false },

      // Address
      { id: "w3", text: "123 Main St", x: 0.3, y: 0.10, width: 0.25, height: 0.025, label: "ADDRESS_LINE", isFlagged: false },
      { id: "w4", text: "Austin, TX 78701", x: 0.25, y: 0.13, width: 0.35, height: 0.025, label: "ADDRESS_LINE", isFlagged: false },

      // Phone
      { id: "w5", text: "(512) 555-0123", x: 0.3, y: 0.16, width: 0.25, height: 0.025, label: "PHONE_NUMBER", isFlagged: false },

      // Date/Time
      { id: "w6", text: "01/15/2025", x: 0.15, y: 0.22, width: 0.18, height: 0.025, label: "DATE", isFlagged: false },
      { id: "w7", text: "2:34 PM", x: 0.65, y: 0.22, width: 0.12, height: 0.025, label: "TIME", isFlagged: false },

      // Line items
      { id: "w8", text: "MILK 2% GAL", x: 0.1, y: 0.30, width: 0.35, height: 0.025, label: "PRODUCT_NAME", isFlagged: false },
      { id: "w9", text: "$4.99", x: 0.75, y: 0.30, width: 0.12, height: 0.025, label: "LINE_TOTAL", isFlagged: false },

      { id: "w10", text: "BREAD WHEAT", x: 0.1, y: 0.35, width: 0.35, height: 0.025, label: "PRODUCT_NAME", isFlagged: false },
      { id: "w11", text: "$3.49", x: 0.75, y: 0.35, width: 0.12, height: 0.025, label: "LINE_TOTAL", isFlagged: false },

      { id: "w12", text: "EGGS LARGE 12CT", x: 0.1, y: 0.40, width: 0.40, height: 0.025, label: "PRODUCT_NAME", isFlagged: false },
      { id: "w13", text: "$5.99", x: 0.75, y: 0.40, width: 0.12, height: 0.025, label: "LINE_TOTAL", isFlagged: false },

      { id: "w14", text: "CHICKEN BREAST", x: 0.1, y: 0.45, width: 0.38, height: 0.025, label: "PRODUCT_NAME", isFlagged: false },
      { id: "w15", text: "$8.99", x: 0.75, y: 0.45, width: 0.12, height: 0.025, label: "LINE_TOTAL", isFlagged: false },

      { id: "w16", text: "APPLES GALA 3LB", x: 0.1, y: 0.50, width: 0.40, height: 0.025, label: "PRODUCT_NAME", isFlagged: false },
      { id: "w17", text: "$4.49", x: 0.75, y: 0.50, width: 0.12, height: 0.025, label: "LINE_TOTAL", isFlagged: false },

      // Totals section
      { id: "w18", text: "SUBTOTAL", x: 0.5, y: 0.60, width: 0.18, height: 0.025, label: "SUBTOTAL", isFlagged: false },
      { id: "w19", text: "$27.95", x: 0.75, y: 0.60, width: 0.12, height: 0.025, label: "SUBTOTAL", isFlagged: false },

      { id: "w20", text: "TAX", x: 0.58, y: 0.65, width: 0.08, height: 0.025, label: "TAX", isFlagged: false },
      { id: "w21", text: "$2.30", x: 0.75, y: 0.65, width: 0.12, height: 0.025, label: "TAX", isFlagged: false },

      // THE FLAGGED WORD - GRAND_TOTAL in wrong position (should be at bottom)
      // This is mislabeled as GRAND_TOTAL but appears too early (y=0.70 instead of expected y=0.75+)
      {
        id: "w22",
        text: "$30.25",
        x: 0.75,
        y: 0.70,
        width: 0.12,
        height: 0.025,
        label: "GRAND_TOTAL",
        isFlagged: true,
        anomalyType: "geometric_anomaly",
        reasoning: "GRAND_TOTAL appears at y=0.70, but expected y=0.78 based on learned patterns from SUBTOTAL. The geometric relationship between SUBTOTAL and GRAND_TOTAL is outside the normal range (z-score: 2.8)."
      },
      { id: "w23", text: "TOTAL", x: 0.55, y: 0.70, width: 0.12, height: 0.025, label: "GRAND_TOTAL", isFlagged: false },

      // Footer
      { id: "w24", text: "THANK YOU", x: 0.35, y: 0.82, width: 0.20, height: 0.025, label: null, isFlagged: false },
      { id: "w25", text: "PLEASE COME AGAIN", x: 0.25, y: 0.86, width: 0.35, height: 0.025, label: null, isFlagged: false },
    ],
  },

  patterns: {
    labelPairs: [
      // SUBTOTAL -> GRAND_TOTAL pattern (the one being violated)
      {
        from: "SUBTOTAL",
        to: "GRAND_TOTAL",
        observations: generateObservations(0.0, 0.12, 0.015, 25),
        mean: { dx: 0.0, dy: 0.12 }, // GRAND_TOTAL typically 0.12 below SUBTOTAL
        std: 0.015,
      },
      // MERCHANT_NAME -> SUBTOTAL pattern
      {
        from: "MERCHANT_NAME",
        to: "SUBTOTAL",
        observations: generateObservations(0.20, 0.55, 0.04, 20),
        mean: { dx: 0.20, dy: 0.55 },
        std: 0.04,
      },
      // TAX -> GRAND_TOTAL pattern
      {
        from: "TAX",
        to: "GRAND_TOTAL",
        observations: generateObservations(0.0, 0.08, 0.012, 22),
        mean: { dx: 0.0, dy: 0.08 }, // GRAND_TOTAL typically 0.08 below TAX
        std: 0.012,
      },
      // SUBTOTAL -> TAX pattern
      {
        from: "SUBTOTAL",
        to: "TAX",
        observations: generateObservations(0.0, 0.05, 0.01, 28),
        mean: { dx: 0.0, dy: 0.05 },
        std: 0.01,
      },
    ],
  },

  flaggedWord: {
    wordId: "w22",
    referenceLabel: "SUBTOTAL",
    expected: { dx: 0.0, dy: 0.12 }, // Expected offset from SUBTOTAL
    actual: { dx: 0.0, dy: 0.10 },   // Actual offset (too close, y difference is 0.70 - 0.60 = 0.10)
    zScore: 2.8,
    threshold: 2.5,
  },
};

export default mockData;
