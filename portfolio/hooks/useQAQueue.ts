import { useEffect, useState, useRef, useCallback } from "react";
import type { QAQuestionData } from "./qaTypes";

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

async function fetchQuestion(index: number): Promise<QAQuestionData | null> {
  try {
    const res = await fetch(`${getApiBase()}/qa/visualization?index=${index}`);
    if (!res.ok) return null;
    const json = await res.json();
    const questions = json.questions;
    return questions?.[0] ?? null;
  } catch {
    return null;
  }
}

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
 * 4. All fetches share a ref-based cache — revisited questions are instant.
 * 5. Re-shuffles when the full set has been shown.
 */
export function useQAQueue(prefetchAhead = 3): UseQAQueueResult {
  const [data, setData] = useState<QAQuestionData | null>(null);
  const [questionIndex, setQuestionIndex] = useState(-1);
  const [totalQuestions, setTotalQuestions] = useState(0);

  const cache = useRef<Map<number, QAQuestionData>>(new Map());
  const order = useRef<number[]>([]);
  const cursor = useRef(0);
  const inflightRef = useRef<Set<number>>(new Set());
  const latestRequestedIndexRef = useRef<number | null>(null);

  // Pre-fetch upcoming questions into the cache (fire-and-forget)
  const prefetch = useCallback(
    (fromCursor: number) => {
      const len = order.current.length;
      if (len === 0) return;
      for (let i = 1; i <= prefetchAhead; i++) {
        const idx = order.current[(fromCursor + i) % len];
        if (cache.current.has(idx) || inflightRef.current.has(idx)) continue;
        inflightRef.current.add(idx);
        fetchQuestion(idx).then((d) => {
          inflightRef.current.delete(idx);
          if (d) cache.current.set(idx, d);
        });
      }
    },
    [prefetchAhead],
  );

  // Load a question by its API index — from cache or fetch
  const loadQuestion = useCallback(
    (idx: number) => {
      latestRequestedIndexRef.current = idx;
      setQuestionIndex(idx);
      const cached = cache.current.get(idx);
      if (cached) {
        setData(cached);
        return;
      }
      setData(null);
      fetchQuestion(idx).then((d) => {
        if (d) {
          if (latestRequestedIndexRef.current !== idx) return;
          cache.current.set(idx, d);
          setData(d);
        }
      });
    },
    [],
  );

  // 1. On mount — fetch metadata, shuffle, kick off first question + pre-fetches
  useEffect(() => {
    let cancelled = false;
    fetch(`${getApiBase()}/qa/visualization`)
      .then((r) => r.json())
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
