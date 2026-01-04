/**
 * Mock data for LLMEvaluatorVisualization
 *
 * Represents the LLM evaluation pipeline showing:
 * - Currency label validation
 * - Metadata label validation
 * - Financial math validation
 */

export type Decision = "VALID" | "INVALID" | "NEEDS_REVIEW";
export type Confidence = "high" | "medium" | "low";

export interface EvaluationResult {
  wordText: string;
  currentLabel: string | null;
  decision: Decision;
  reasoning: string;
  suggestedLabel?: string;
  confidence: Confidence;
}

export interface FinancialMathResult {
  equation: string;
  subtotal: number;
  tax: number;
  expectedTotal: number;
  actualTotal: number;
  difference: number;
  decision: Decision;
  reasoning: string;
  wrongValue: "SUBTOTAL" | "TAX" | "GRAND_TOTAL" | null;
}

export interface PipelineStage {
  id: string;
  name: string;
  status: "pending" | "processing" | "complete";
}

export interface LLMEvaluatorData {
  receipt: {
    merchantName: string;
    lineItems: Array<{ name: string; price: number }>;
    subtotal: number;
    tax: number;
    grandTotal: number;
  };
  evaluations: {
    currency: EvaluationResult[];
    metadata: EvaluationResult[];
    financial: FinancialMathResult;
  };
  pipeline: PipelineStage[];
}

// Decision colors
export const DECISION_COLORS: Record<Decision, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  NEEDS_REVIEW: "var(--color-yellow)",
};

export const CONFIDENCE_OPACITY: Record<Confidence, number> = {
  high: 1,
  medium: 0.8,
  low: 0.6,
};

export const mockData: LLMEvaluatorData = {
  receipt: {
    merchantName: "ACME Grocery",
    lineItems: [
      { name: "MILK 2% GAL", price: 4.99 },
      { name: "BREAD WHEAT", price: 3.49 },
      { name: "EGGS LARGE 12CT", price: 5.99 },
      { name: "CHICKEN BREAST", price: 8.99 },
      { name: "APPLES GALA 3LB", price: 4.49 },
    ],
    subtotal: 27.95,
    tax: 2.30,
    grandTotal: 30.25, // Note: This is correct (27.95 + 2.30 = 30.25)
  },

  evaluations: {
    currency: [
      {
        wordText: "$4.99",
        currentLabel: "LINE_TOTAL",
        decision: "VALID",
        reasoning: "Price format and position match LINE_TOTAL pattern for this merchant.",
        confidence: "high",
      },
      {
        wordText: "$3.49",
        currentLabel: "LINE_TOTAL",
        decision: "VALID",
        reasoning: "Consistent with other line item prices on this receipt.",
        confidence: "high",
      },
      {
        wordText: "$27.95",
        currentLabel: "SUBTOTAL",
        decision: "VALID",
        reasoning: "Sum of line items matches this value. Position is correct for SUBTOTAL.",
        confidence: "high",
      },
      {
        wordText: "$2.30",
        currentLabel: "TAX",
        decision: "VALID",
        reasoning: "Tax rate of 8.23% is reasonable for this location.",
        confidence: "medium",
      },
      {
        wordText: "$30.25",
        currentLabel: "GRAND_TOTAL",
        decision: "VALID",
        reasoning: "SUBTOTAL + TAX = $27.95 + $2.30 = $30.25. Math checks out.",
        confidence: "high",
      },
    ],
    metadata: [
      {
        wordText: "ACME",
        currentLabel: "MERCHANT_NAME",
        decision: "VALID",
        reasoning: "Matches Google Places data for this location.",
        confidence: "high",
      },
      {
        wordText: "GROCERY",
        currentLabel: "MERCHANT_NAME",
        decision: "VALID",
        reasoning: "Part of merchant name, consistent with header position.",
        confidence: "high",
      },
      {
        wordText: "123 Main St",
        currentLabel: "ADDRESS_LINE",
        decision: "VALID",
        reasoning: "Address format matches, verified against Google Places.",
        confidence: "high",
      },
      {
        wordText: "(512) 555-0123",
        currentLabel: "PHONE_NUMBER",
        decision: "VALID",
        reasoning: "Valid phone number format, area code matches location.",
        confidence: "high",
      },
      {
        wordText: "01/15/2025",
        currentLabel: "DATE",
        decision: "VALID",
        reasoning: "Standard date format MM/DD/YYYY, reasonable date.",
        confidence: "high",
      },
    ],
    financial: {
      equation: "GRAND_TOTAL = SUBTOTAL + TAX",
      subtotal: 27.95,
      tax: 2.30,
      expectedTotal: 30.25,
      actualTotal: 30.25,
      difference: 0,
      decision: "VALID",
      reasoning: "All financial math checks passed. SUBTOTAL ($27.95) + TAX ($2.30) = GRAND_TOTAL ($30.25).",
      wrongValue: null,
    },
  },

  pipeline: [
    { id: "input", name: "Load Receipt", status: "complete" },
    { id: "currency", name: "Currency Review", status: "complete" },
    { id: "metadata", name: "Metadata Review", status: "complete" },
    { id: "financial", name: "Financial Math", status: "complete" },
    { id: "output", name: "Apply Decisions", status: "complete" },
  ],
};

// Alternative mock data with an error case for demonstration
export const mockDataWithError: LLMEvaluatorData = {
  receipt: {
    merchantName: "ACME Grocery",
    lineItems: [
      { name: "MILK 2% GAL", price: 4.99 },
      { name: "BREAD WHEAT", price: 3.49 },
      { name: "EGGS LARGE 12CT", price: 5.99 },
      { name: "CHICKEN BREAST", price: 8.99 },
      { name: "APPLES GALA 3LB", price: 4.49 },
    ],
    subtotal: 27.95,
    tax: 2.30,
    grandTotal: 30.75, // ERROR: Should be 30.25, OCR misread 2 as 7
  },

  evaluations: {
    currency: [
      {
        wordText: "$4.99",
        currentLabel: "LINE_TOTAL",
        decision: "VALID",
        reasoning: "Price format and position match LINE_TOTAL pattern.",
        confidence: "high",
      },
      {
        wordText: "$27.95",
        currentLabel: "SUBTOTAL",
        decision: "VALID",
        reasoning: "Sum of line items matches this value.",
        confidence: "high",
      },
      {
        wordText: "$2.30",
        currentLabel: "TAX",
        decision: "VALID",
        reasoning: "Tax rate is reasonable for this location.",
        confidence: "medium",
      },
      {
        wordText: "$30.75",
        currentLabel: "GRAND_TOTAL",
        decision: "INVALID",
        reasoning: "OCR likely misread '2' as '7'. Expected $30.25 based on SUBTOTAL + TAX.",
        suggestedLabel: "GRAND_TOTAL",
        confidence: "high",
      },
    ],
    metadata: [
      {
        wordText: "ACME GROCERY",
        currentLabel: "MERCHANT_NAME",
        decision: "VALID",
        reasoning: "Matches Google Places data.",
        confidence: "high",
      },
      {
        wordText: "(512) 555-0123",
        currentLabel: "PHONE_NUMBER",
        decision: "VALID",
        reasoning: "Valid phone format.",
        confidence: "high",
      },
    ],
    financial: {
      equation: "GRAND_TOTAL = SUBTOTAL + TAX",
      subtotal: 27.95,
      tax: 2.30,
      expectedTotal: 30.25,
      actualTotal: 30.75,
      difference: 0.50,
      decision: "INVALID",
      reasoning: "Math mismatch: $27.95 + $2.30 = $30.25, but GRAND_TOTAL shows $30.75. Difference of $0.50 suggests OCR error - '2' likely misread as '7'.",
      wrongValue: "GRAND_TOTAL",
    },
  },

  pipeline: [
    { id: "input", name: "Load Receipt", status: "complete" },
    { id: "currency", name: "Currency Review", status: "complete" },
    { id: "metadata", name: "Metadata Review", status: "complete" },
    { id: "financial", name: "Financial Math", status: "complete" },
    { id: "output", name: "Apply Decisions", status: "complete" },
  ],
};

export default mockDataWithError; // Use the error case for more interesting demo
