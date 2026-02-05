import { useEffect, useState, useRef } from "react";
import type { QAQuestionData } from "./qaTypes";

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
