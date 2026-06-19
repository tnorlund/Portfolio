import { useRouter } from "next/router";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../services/api";
import {
  getAnalyticsSessionPageViews,
  trackEvent,
} from "../../utils/analytics";
import styles from "./ReaderInsight.module.css";

const BASELINE_URL = "/analytics/reader-baselines.json";
const MIN_SCROLLABLE_DISTANCE = 900;
const MIN_BASELINE_SAMPLE_SIZE = 5;

type ReaderBaseline = {
  averageTimeToBottomMs?: number;
  sampleSize?: number;
  updatedAt?: string;
};

type ReaderBaselineFile = {
  default?: ReaderBaseline;
  pages?: Record<string, ReaderBaseline>;
};

type ReaderResult = {
  timeToBottomMs: number;
  activeScrollMs: number;
  pageHeight: number;
  scrollablePixels: number;
  screensPerMinute: number;
  sessionPageViews: number;
  quickJump: boolean;
};

function normalizePath(path: string): string {
  const pathWithoutQuery = path.split("?")[0];
  const normalized = pathWithoutQuery.replace(/\/$/, "");

  return normalized || "/";
}

function getScrollableDistance(): number {
  const documentElement = document.documentElement;
  const body = document.body;
  const scrollHeight = Math.max(
    documentElement.scrollHeight,
    body?.scrollHeight ?? 0
  );

  return Math.max(0, scrollHeight - window.innerHeight);
}

function formatDuration(milliseconds: number): string {
  const totalSeconds = Math.max(1, Math.round(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;

  if (minutes === 0) {
    return `${seconds}s`;
  }

  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
}

function getUsableBaseline(
  baseline: ReaderBaseline | null
): ReaderBaseline | null {
  if (
    !baseline?.averageTimeToBottomMs ||
    baseline.averageTimeToBottomMs <= 0 ||
    !baseline.sampleSize ||
    baseline.sampleSize < MIN_BASELINE_SAMPLE_SIZE
  ) {
    return null;
  }

  return baseline;
}

function resolveBaseline(
  baselineFile: ReaderBaselineFile,
  path: string
): ReaderBaseline | null {
  return (
    baselineFile.pages?.[path] ??
    baselineFile.pages?.[`${path}/`] ??
    baselineFile.default ??
    null
  );
}

function getComparisonText(
  result: ReaderResult,
  baseline: ReaderBaseline | null
): string {
  const usableBaseline = getUsableBaseline(baseline);

  if (!usableBaseline?.averageTimeToBottomMs) {
    return `You reached the bottom in ${formatDuration(
      result.timeToBottomMs
    )}. The average will appear here after a few completed reads.`;
  }

  const deltaPercent = Math.round(
    ((usableBaseline.averageTimeToBottomMs - result.timeToBottomMs) /
      usableBaseline.averageTimeToBottomMs) *
      100
  );
  const absoluteDelta = Math.abs(deltaPercent);

  if (absoluteDelta < 3) {
    return `You reached the bottom in ${formatDuration(
      result.timeToBottomMs
    )}, almost exactly average.`;
  }

  const direction = deltaPercent > 0 ? "faster" : "slower";

  return `You reached the bottom ${absoluteDelta}% ${direction} than the average reader.`;
}

function getPaceText(result: ReaderResult): string {
  if (result.quickJump) {
    return "Quick jump";
  }

  return `${result.screensPerMinute.toFixed(1)} screens/min`;
}

export default function ReaderInsight() {
  const router = useRouter();
  const sectionRef = useRef<HTMLElement | null>(null);
  const startedAtRef = useRef(0);
  const firstScrollAtRef = useRef<number | null>(null);
  const hasTrackedSummaryRef = useRef(false);
  const [hasPageDepth, setHasPageDepth] = useState(false);
  const [baseline, setBaseline] = useState<ReaderBaseline | null>(null);
  const [liveBaseline, setLiveBaseline] =
    useState<ReaderBaseline | null>(null);
  const [result, setResult] = useState<ReaderResult | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    startedAtRef.current = performance.now();
    firstScrollAtRef.current = null;
    hasTrackedSummaryRef.current = false;
    setResult(null);
    setLiveBaseline(null);

    const updatePageDepth = () => {
      setHasPageDepth(getScrollableDistance() >= MIN_SCROLLABLE_DISTANCE);
    };

    updatePageDepth();
    window.setTimeout(updatePageDepth, 250);
    window.addEventListener("resize", updatePageDepth);

    return () => {
      window.removeEventListener("resize", updatePageDepth);
    };
  }, [router.asPath]);

  useEffect(() => {
    const normalizedPath = normalizePath(router.asPath);
    let isActive = true;

    setBaseline(null);

    window
      .fetch(BASELINE_URL, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) {
          return null;
        }

        return response.json() as Promise<ReaderBaselineFile>;
      })
      .then((baselineFile) => {
        if (!isActive || !baselineFile) {
          return;
        }

        setBaseline(resolveBaseline(baselineFile, normalizedPath));
      })
      .catch(() => {
        if (isActive) {
          setBaseline(null);
        }
      });

    return () => {
      isActive = false;
    };
  }, [router.asPath]);

  useEffect(() => {
    const markFirstScroll = () => {
      if (firstScrollAtRef.current === null) {
        firstScrollAtRef.current = performance.now();
      }
    };

    window.addEventListener("scroll", markFirstScroll, { passive: true });

    return () => {
      window.removeEventListener("scroll", markFirstScroll);
    };
  }, []);

  const trackReaderSummary = useCallback(() => {
    if (hasTrackedSummaryRef.current) {
      return;
    }

    hasTrackedSummaryRef.current = true;

    const now = performance.now();
    const activeScrollStart =
      firstScrollAtRef.current ?? startedAtRef.current;
    const timeToBottomMs = Math.round(now - startedAtRef.current);
    const activeScrollMs = Math.round(now - activeScrollStart);
    const pageHeight = document.documentElement.scrollHeight;
    const scrollablePixels = getScrollableDistance();
    const screensPerMinute =
      activeScrollMs > 0
        ? (scrollablePixels / window.innerHeight) /
          (activeScrollMs / 60000)
        : 0;
    const quickJump = activeScrollMs < 5000;
    const sessionPageViews = getAnalyticsSessionPageViews();
    const usableBaseline = getUsableBaseline(baseline);
    const readerDeltaPercent = usableBaseline?.averageTimeToBottomMs
      ? Math.round(
          ((usableBaseline.averageTimeToBottomMs - timeToBottomMs) /
            usableBaseline.averageTimeToBottomMs) *
            100
        )
      : undefined;

    const nextResult = {
      timeToBottomMs,
      activeScrollMs,
      pageHeight,
      scrollablePixels,
      screensPerMinute,
      sessionPageViews,
      quickJump,
    };

    setResult(nextResult);

    const eventParams = {
      page_path: router.asPath,
      time_to_bottom_ms: timeToBottomMs,
      active_scroll_ms: activeScrollMs,
      page_height: pageHeight,
      scrollable_pixels: scrollablePixels,
      screens_per_minute: Number(screensPerMinute.toFixed(2)),
      reader_delta_percent: readerDeltaPercent,
      baseline_sample_size: baseline?.sampleSize,
      session_page_views: sessionPageViews,
      quick_jump: quickJump,
    };
    const analyticsMeta = trackEvent("reader_summary", eventParams);

    api
      .submitReaderSummary({
        page_path: router.asPath,
        analytics_session_id: analyticsMeta.sessionId,
        analytics_event_id: analyticsMeta.eventId,
        time_to_bottom_ms: timeToBottomMs,
        active_scroll_ms: activeScrollMs,
        page_height: pageHeight,
        scrollable_pixels: scrollablePixels,
        screens_per_minute: Number(screensPerMinute.toFixed(2)),
        quick_jump: quickJump,
      })
      .then((response) => {
        setLiveBaseline({
          averageTimeToBottomMs:
            response.comparison.averageTimeToBottomMs ?? undefined,
          sampleSize: response.comparison.sampleSize,
          updatedAt: response.aggregate.updatedAt ?? undefined,
        });
      })
      .catch(() => {
        // The local result and GA/CloudFront events are still useful.
      });
  }, [baseline, router.asPath]);

  useEffect(() => {
    if (!hasPageDepth || !sectionRef.current) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          trackReaderSummary();
        }
      },
      {
        threshold: 0.35,
      }
    );

    observer.observe(sectionRef.current);

    return () => {
      observer.disconnect();
    };
  }, [hasPageDepth, trackReaderSummary]);

  if (!hasPageDepth) {
    return null;
  }

  const effectiveBaseline = liveBaseline ?? baseline;
  const comparisonText = result
    ? getComparisonText(result, effectiveBaseline)
    : "Calculating your read.";
  const baselineSampleSize = effectiveBaseline?.sampleSize ?? 0;

  return (
    <section
      ref={sectionRef}
      className={styles.readerInsight}
      aria-label="Reading pace"
    >
      <p className={styles.label}>Reading pace</p>
      <p className={styles.summary}>{comparisonText}</p>
      {result && (
        <dl className={styles.metrics}>
          <div className={styles.metric}>
            <dt>Your time</dt>
            <dd>{formatDuration(result.timeToBottomMs)}</dd>
          </div>
          <div className={styles.metric}>
            <dt>Active pace</dt>
            <dd>{getPaceText(result)}</dd>
          </div>
          <div className={styles.metric}>
            <dt>This session</dt>
            <dd>{result.sessionPageViews} pages</dd>
          </div>
          <div className={styles.metric}>
            <dt>Average sample</dt>
            <dd>
              {baselineSampleSize >= MIN_BASELINE_SAMPLE_SIZE
                ? `${baselineSampleSize} reads`
                : "Collecting"}
            </dd>
          </div>
        </dl>
      )}
    </section>
  );
}
