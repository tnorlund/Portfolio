/**
 * Mock data for QAAgentFlow Playwright tests.
 *
 * Each question has a distinct answer that cannot be confused with the
 * EXAMPLE_TRACE fallback answer ("You spent $58.38 on coffee").
 *
 * All trace steps use durationMs: 0 so the component falls back to
 * DEFAULT_STEP_MS (1500ms), keeping animation cycles predictable.
 */

/** Metadata response for GET /qa/visualization (no ?index param) */
export const mockQAMetadata = {
  metadata: { total_questions: 3 },
  questions: [],
};

/** Question 0 — grocery spending */
const question0 = {
  question: "How much did I spend on groceries?",
  questionIndex: 0,
  traceId: "trace-mock-0",
  trace: [
    {
      type: "plan" as const,
      content: "spending_category → keyword_search",
      detail: "Classify question and choose retrieval strategy",
      durationMs: 0,
    },
    {
      type: "agent" as const,
      content: "Search for grocery receipts",
      detail: "Deciding which tools to call based on classification",
      durationMs: 0,
    },
    {
      type: "synthesize" as const,
      content: [
        "# Grocery spending",
        "",
        "You spent $42.00 on groceries across receipts with valid dates and reasonable totals, excluding extreme OCR outliers so the summary reflects typical spending.",
      ].join("\n"),
      detail: "3 receipts with grocery items identified",
      durationMs: 0,
      receipts: [
        {
          imageId: "evidence-receipt-a",
          merchant: "Neighborhood Market",
          item: "Organic Milk",
          amount: 6.49,
          thumbnailKey: "assets/evidence-receipt-a.webp",
          width: 300,
          height: 900,
        },
        {
          imageId: "evidence-receipt-a",
          merchant: "Neighborhood Market",
          item: "Greek Yogurt",
          amount: 5.29,
          thumbnailKey: "assets/evidence-receipt-a-duplicate.webp",
          width: 300,
          height: 900,
        },
        {
          imageId: "evidence-receipt-b",
          merchant: "Corner Grocer",
          item: "Coffee",
          amount: 12.99,
          thumbnailKey: "assets/evidence-receipt-b.webp",
          width: 320,
          height: 960,
        },
      ],
      structuredData: [
        {
          merchant: "Neighborhood Market",
          items: [{ name: "Organic Milk", amount: 6.49 }],
        },
      ],
    },
  ],
  stats: {
    llmCalls: 2,
    toolInvocations: 1,
    receiptsProcessed: 3,
    cost: 0.003,
  },
};

/** Question 1 — restaurant average */
const question1 = {
  question: "What is my average restaurant bill?",
  questionIndex: 1,
  traceId: "trace-mock-1",
  trace: [
    {
      type: "plan" as const,
      content: "aggregation → semantic_search",
      detail: "Classify question and choose retrieval strategy",
      durationMs: 0,
    },
    {
      type: "agent" as const,
      content: "Search for restaurant receipts",
      detail: "Deciding which tools to call based on classification",
      durationMs: 0,
    },
    {
      type: "synthesize" as const,
      content: "Your average restaurant bill is $35.50",
      detail: "7 receipts with restaurant items identified",
      durationMs: 0,
    },
  ],
  stats: {
    llmCalls: 2,
    toolInvocations: 1,
    receiptsProcessed: 7,
    cost: 0.004,
  },
};

/** Question 2 — beverage spending */
const question2 = {
  question: "How much did I spend on beverages?",
  questionIndex: 2,
  traceId: "trace-mock-2",
  trace: [
    {
      type: "plan" as const,
      content: "spending_category → keyword_search",
      detail: "Classify question and choose retrieval strategy",
      durationMs: 0,
    },
    {
      type: "agent" as const,
      content: "Search for beverage receipts",
      detail: "Deciding which tools to call based on classification",
      durationMs: 0,
    },
    {
      type: "synthesize" as const,
      content: "You spent $22.75 on beverages",
      detail: "4 receipts with beverage items identified",
      durationMs: 0,
    },
  ],
  stats: {
    llmCalls: 2,
    toolInvocations: 1,
    receiptsProcessed: 4,
    cost: 0.003,
  },
};

/** All 3 mock questions indexed by questionIndex */
export const mockQAQuestions = [question0, question1, question2];

/** A trace with two tool calls sharing the same wall-clock interval. */
export const mockParallelQAQuestion = {
  question: "Which grocery searches ran together?",
  questionIndex: 0,
  traceId: "trace-parallel-tools",
  trace: [
    {
      type: "plan" as const,
      content: "comparison → parallel_search",
      durationMs: 1000,
      startOffsetMs: 0,
    },
    {
      type: "agent" as const,
      content: "Run complementary grocery searches",
      durationMs: 1000,
      startOffsetMs: 1000,
    },
    {
      type: "tools" as const,
      content: "search_receipts",
      durationMs: 2000,
      startOffsetMs: 2000,
    },
    {
      type: "tools" as const,
      content: "search_receipt_descriptions",
      durationMs: 1200,
      startOffsetMs: 2000,
    },
    {
      type: "synthesize" as const,
      content: "Both searches completed in parallel",
      durationMs: 1000,
      startOffsetMs: 4000,
    },
  ],
  stats: {
    llmCalls: 2,
    toolInvocations: 2,
    receiptsProcessed: 4,
    cost: 0.004,
  },
};

/** All distinct answer texts — useful for assertions */
export const MOCK_ANSWERS = [
  "You spent $42.00 on groceries",
  "Your average restaurant bill is $35.50",
  "You spent $22.75 on beverages",
] as const;

/** The EXAMPLE_TRACE answer text (hardcoded in QAAgentFlow.tsx) */
export const EXAMPLE_TRACE_ANSWER = "You spent $58.38 on coffee";
