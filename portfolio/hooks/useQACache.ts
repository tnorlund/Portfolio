import { useEffect, useState, useRef } from "react";

/** Trace step from the QA cache API */
interface TraceStep {
  type: "plan" | "agent" | "tools" | "shape" | "synthesize";
  content: string;
  detail?: string;
  /** Step duration in milliseconds (from LangSmith trace timestamps) */
  durationMs?: number;
  receipts?: {
    imageId: string;
    merchant: string;
    item: string;
    amount: number;
    thumbnailKey: string;
    width: number;
    height: number;
  }[];
  structuredData?: {
    merchant: string;
    items: { name: string; amount: number }[];
  }[];
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

interface UseQACacheResult {
  data: QAQuestionData | null;
  loading: boolean;
  error: Error | null;
}

function getApiBase(): string {
  if (typeof window === "undefined") return "";
  const host = window.location.hostname;
  if (
    host === "localhost" ||
    host.startsWith("127.") ||
    host.startsWith("192.168.") ||
    host.startsWith("10.") ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(host)
  ) {
    return "https://dev-api.tylernorlund.com";
  }
  return "";
}

/**
 * Fetches a single question's trace data from the QA visualization cache API.
 * Caches results in a ref to avoid refetching on re-render with the same index.
 */
export function useQACache(questionIndex?: number): UseQACacheResult {
  const [data, setData] = useState<QAQuestionData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const cache = useRef<Map<number, QAQuestionData>>(new Map());

  useEffect(() => {
    if (questionIndex === undefined || questionIndex < 0) return;

    // Check cache first
    const cached = cache.current.get(questionIndex);
    if (cached) {
      setData(cached);
      return;
    }

    let isMounted = true;
    setLoading(true);
    setError(null);

    const fetchData = async () => {
      try {
        const response = await fetch(
          `${getApiBase()}/qa/visualization?index=${questionIndex}`
        );
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const json = await response.json();
        const questions = json.questions;
        if (questions && questions.length > 0) {
          const questionData = questions[0] as QAQuestionData;
          cache.current.set(questionIndex, questionData);
          if (isMounted) {
            setData(questionData);
          }
        }
      } catch (err) {
        if (isMounted) {
          setError(err as Error);
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };

    fetchData();
    return () => {
      isMounted = false;
    };
  }, [questionIndex]);

  return { data, loading, error };
}
