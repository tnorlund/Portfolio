import { useEffect, useState, useRef, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { QAQuestionData } from "./qaTypes";
import { API_CONFIG } from "../services/api/config";

async function fetchQuestion(index: number): Promise<QAQuestionData> {
  const res = await fetch(
    `${API_CONFIG.baseUrl}/qa/visualization?index=${index}`
  );
  if (!res.ok) throw new Error(`Failed to fetch question ${index}`);
  const json = await res.json();
  const question = json.questions?.[0];
  if (!question) throw new Error(`No question at index ${index}`);
  return question;
}

const questionQuery = (index: number) => ({
  queryKey: ["qa", "question", index],
  queryFn: () => fetchQuestion(index),
  // Questions are static content — never refetch one we already have
  staleTime: Infinity,
});

function shuffle(arr: number[]): number[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

interface UseQAQueueResult {
  /** Current question data (null while loading) */
  data: QAQuestionData | null;
  /** Actual question index (0-based, maps to the API index) */
  questionIndex: number;
  /** Advance to the next random question */
  advance: () => void;
  /** Jump to a specific question index (e.g. from marquee click) */
  selectQuestion: (index: number) => void;
  /** Total available questions from the API */
  totalQuestions: number;
}

/**
 * Manages a randomised queue of QA questions with pre-fetching.
 *
 * 1. Fetches metadata to learn the total question count.
 * 2. Shuffles the indices into a random play order.
 * 3. Pre-fetches `prefetchAhead` questions so the next one is ready
 *    by the time the current animation finishes.
 * 4. Caching, deduplication and retries are handled by TanStack Query —
 *    revisited questions are instant, even across remounts.
 * 5. Re-shuffles when the full set has been shown.
 */
export function useQAQueue(prefetchAhead = 3): UseQAQueueResult {
  const queryClient = useQueryClient();
  const [data, setData] = useState<QAQuestionData | null>(null);
  const [questionIndex, setQuestionIndex] = useState(-1);
  const [totalQuestions, setTotalQuestions] = useState(0);

  const order = useRef<number[]>([]);
  const cursor = useRef(0);
  const latestRequestedIndexRef = useRef<number | null>(null);

  // Pre-fetch upcoming questions into the query cache (fire-and-forget;
  // prefetchQuery dedupes in-flight requests and swallows errors)
  const prefetch = useCallback(
    (fromCursor: number) => {
      const len = order.current.length;
      if (len === 0) return;
      for (let i = 1; i <= prefetchAhead; i++) {
        const idx = order.current[(fromCursor + i) % len];
        queryClient.prefetchQuery(questionQuery(idx));
      }
    },
    [prefetchAhead, queryClient],
  );

  // Load a question by its API index — from cache or fetch
  const loadQuestion = useCallback(
    (idx: number) => {
      latestRequestedIndexRef.current = idx;
      setQuestionIndex(idx);
      const cached = queryClient.getQueryData<QAQuestionData>(
        questionQuery(idx).queryKey,
      );
      if (cached) {
        setData(cached);
        return;
      }
      setData(null);
      queryClient
        .fetchQuery(questionQuery(idx))
        .then((d) => {
          if (latestRequestedIndexRef.current !== idx) return;
          setData(d);
        })
        .catch(() => {
          // Leave data null; the next advance/select will retry
        });
    },
    [queryClient],
  );

  // 1. On mount — fetch metadata, shuffle, kick off first question + pre-fetches
  useEffect(() => {
    let cancelled = false;
    queryClient
      .fetchQuery({
        queryKey: ["qa", "metadata"],
        queryFn: async () => {
          const r = await fetch(`${API_CONFIG.baseUrl}/qa/visualization`);
          if (!r.ok) throw new Error("Failed to fetch QA metadata");
          return r.json();
        },
        staleTime: Infinity,
      })
      .then((json) => {
        if (cancelled) return;
        const total: number = json?.metadata?.total_questions ?? 32;
        setTotalQuestions(total);
        const indices = Array.from({ length: total }, (_, i) => i);
        order.current = shuffle(indices);
        cursor.current = 0;
        loadQuestion(order.current[0]);
        prefetch(0);
      })
      .catch(() => {
        // Fallback: use 32 sequential
        if (cancelled) return;
        setTotalQuestions(32);
        order.current = shuffle(Array.from({ length: 32 }, (_, i) => i));
        cursor.current = 0;
        loadQuestion(order.current[0]);
        prefetch(0);
      });
    return () => {
      cancelled = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Advance to the next random question
  const advance = useCallback(() => {
    const len = order.current.length;
    if (len === 0) return;
    let next = cursor.current + 1;
    if (next >= len) {
      // Re-shuffle for the next cycle
      order.current = shuffle(order.current);
      next = 0;
    }
    cursor.current = next;
    loadQuestion(order.current[next]);
    prefetch(next);
  }, [loadQuestion, prefetch]);

  // Jump to a specific question (marquee click)
  const selectQuestion = useCallback(
    (idx: number) => {
      // Find it in the current order, or just load directly
      const pos = order.current.indexOf(idx);
      if (pos >= 0) cursor.current = pos;
      loadQuestion(idx);
      prefetch(cursor.current);
    },
    [loadQuestion, prefetch],
  );

  return { data, questionIndex, advance, selectQuestion, totalQuestions };
}
