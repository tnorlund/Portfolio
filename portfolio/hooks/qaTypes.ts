export type StepType = "plan" | "agent" | "tools" | "shape" | "synthesize";

export interface ReceiptEvidence {
  imageId: string;
  merchant: string;
  item: string;
  amount: number;
  thumbnailKey: string;
  width: number;
  height: number;
}

export interface StructuredReceipt {
  merchant: string;
  items: { name: string; amount: number }[];
}

/** Trace step from the QA cache API */
export interface TraceStep {
  type: StepType;
  content: string;
  detail?: string;
  /** Step duration in milliseconds (from LangSmith trace timestamps) */
  durationMs?: number;
  receipts?: ReceiptEvidence[];
  structuredData?: StructuredReceipt[];
}

/** Per-question data from the QA cache API */
export interface QAQuestionData {
  question: string;
  questionIndex: number;
  traceId?: string;
  trace: TraceStep[];
  stats: {
    llmCalls: number;
    toolInvocations: number;
    receiptsProcessed: number;
    cost: number;
  };
}
