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
      content: "You spent $42.00 on groceries",
      detail: "3 receipts with grocery items identified",
      durationMs: 0,
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

/** All distinct answer texts — useful for assertions */
export const MOCK_ANSWERS = [
  "You spent $42.00 on groceries",
  "Your average restaurant bill is $35.50",
  "You spent $22.75 on beverages",
] as const;

/** The EXAMPLE_TRACE answer text (hardcoded in QAAgentFlow.tsx) */
export const EXAMPLE_TRACE_ANSWER = "You spent $58.38 on coffee";
