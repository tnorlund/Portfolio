import React from "react";
import Image from "next/image";
import ReactMarkdown from "react-markdown";
import { useSpring, animated } from "@react-spring/web";
import { getReceiptMotionScale } from "./ReceiptFlow/receiptFlowUtils";
import type {
  QAQuestionData,
  ReceiptEvidence,
  StepType,
  TraceStep,
  StructuredReceipt,
} from "../../../hooks/qaTypes";
import { useRevealInView } from "../../../hooks/useOptimizedInView";
import { getCdnBaseUrl } from "../../../utils/cdnBase";
import { getJpegFallbackUrl } from "../../../utils/imageFormat";
import {
  buildTimelineLayout,
  buildTimelinePlayback,
  getActiveTimelineStepIndices,
  getTimelineRevealPercent,
} from "./qaTimeline";

interface QAAgentFlowProps {
  /** Whether to auto-play the animation */
  autoPlay?: boolean;
  /** Optional real data from the cache API; falls back to EXAMPLE_TRACE */
  questionData?: QAQuestionData;
  /** Called when one animation cycle completes (after the end-of-trace pause) */
  onCycleComplete?: () => void;
  /** Content rendered at the top of the card (e.g. QuestionMarquee) */
  children?: React.ReactNode;
}

const CDN_BASE = getCdnBaseUrl();
const MAX_EVIDENCE_THUMBNAILS = 8;
const MAX_STRUCTURED_RECEIPTS = 4;
const MAX_ANSWER_SUMMARY_BODY_CHARS = 96;
const CURRENCY_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const getCdnUrl = (key: string): string =>
  `${CDN_BASE}/${key.replace(/^\/+/, "")}`;

const getReceiptJpegFallbackUrl = (key: string): string => {
  const jpegKey = key.replace(/\.(?:avif|webp)$/i, ".jpg");
  return getJpegFallbackUrl({ cdn_s3_key: jpegKey.replace(/^\/+/, "") });
};

const getAnswerSummaryMarkdown = (answer: string): string => {
  const blocks = answer
    .split(/\n\s*\n/)
    .map((block) => block.trim())
    .filter(Boolean);
  const firstBlock = blocks[0] ?? answer;
  const isHeading = (block: string): boolean => /^#{1,6}\s+\S/.test(block);

  const summarizeBlock = (block: string): string => {
    const normalized = block.replace(/\s+/g, " ").trim();
    if (normalized.length <= MAX_ANSWER_SUMMARY_BODY_CHARS) return normalized;

    const sentence = normalized.match(
      new RegExp(`^.{1,${MAX_ANSWER_SUMMARY_BODY_CHARS}}?[.!?](?=\\s|$)`),
    )?.[0];
    if (sentence) return sentence;

    const wordBoundary = normalized
      .slice(0, MAX_ANSWER_SUMMARY_BODY_CHARS + 1)
      .lastIndexOf(" ");
    const cutoff = wordBoundary > 0 ? wordBoundary : MAX_ANSWER_SUMMARY_BODY_CHARS;
    return `${normalized.slice(0, cutoff).trimEnd()}…`;
  };

  if (!isHeading(firstBlock)) return summarizeBlock(firstBlock);

  const firstSubstantiveBlock = blocks
    .slice(1)
    .find((block) => !isHeading(block));
  return firstSubstantiveBlock
    ? `${firstBlock}\n\n${summarizeBlock(firstSubstantiveBlock)}`
    : firstBlock;
};

const formatTimelineBarDuration = (ms: number): string => {
  if (ms >= 1000) {
    const seconds = ms / 1000;
    return `${Number.isInteger(seconds) ? seconds.toFixed(0) : seconds.toFixed(1)}s`;
  }

  return `${Math.max(1, Math.round(ms))}ms`;
};

const deduplicateReceiptEvidence = (trace: TraceStep[]): ReceiptEvidence[] => {
  const seenImageIds = new Set<string>();
  const receipts: ReceiptEvidence[] = [];

  for (const step of trace) {
    for (const receipt of step.receipts ?? []) {
      if (!receipt.imageId || !receipt.thumbnailKey) continue;
      if (seenImageIds.has(receipt.imageId)) continue;

      seenImageIds.add(receipt.imageId);
      receipts.push(receipt);
    }
  }

  return receipts;
};

interface ReceiptEvidenceThumbnailProps {
  receipt: ReceiptEvidence;
  index: number;
  isVisible: boolean;
  thumbnailHeight: number;
  leftOffset: number;
}

const EVIDENCE_THUMBNAIL_HEIGHT = 154;
const EVIDENCE_STACK_EDGE_SAFETY = 16;
const MAX_EVIDENCE_OVERLAP_RATIO = 0.82;

const ReceiptEvidenceThumbnail: React.FC<ReceiptEvidenceThumbnailProps> = ({
  receipt,
  index,
  isVisible,
  thumbnailHeight,
  leftOffset,
}) => {
  const initialUrl = getCdnUrl(receipt.thumbnailKey);
  const fallbackUrl = getReceiptJpegFallbackUrl(receipt.thumbnailKey);
  const [currentUrl, setCurrentUrl] = React.useState(initialUrl);
  const [hasErrored, setHasErrored] = React.useState(false);

  React.useEffect(() => {
    setCurrentUrl(initialUrl);
    setHasErrored(false);
  }, [initialUrl]);

  const handleError = React.useCallback(() => {
    if (currentUrl !== fallbackUrl) {
      setCurrentUrl(fallbackUrl);
      return;
    }
    setHasErrored(true);
  }, [currentUrl, fallbackUrl]);

  const rotation = ((index % 5) - 2) * 1.8;
  const merchant = receipt.merchant || "Unknown merchant";
  const sourceWidth = receipt.width > 0 ? receipt.width : 300;
  const sourceHeight = receipt.height > 0 ? receipt.height : 900;
  const sourceAspectRatio = sourceWidth / sourceHeight;
  const thumbnailWidth = thumbnailHeight * sourceAspectRatio;
  const evidenceDetail = [
    receipt.item,
    Number.isFinite(receipt.amount)
      ? CURRENCY_FORMATTER.format(receipt.amount)
      : undefined,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      role="listitem"
      data-testid="receipt-evidence-thumbnail"
      data-source-aspect-ratio={sourceAspectRatio}
      title={evidenceDetail ? `${merchant}: ${evidenceDetail}` : merchant}
      style={
        {
          position: "absolute",
          left: `${leftOffset}px`,
          top: 0,
          width: `${thumbnailWidth}px`,
          minWidth: `${thumbnailWidth}px`,
          height: `${thumbnailHeight}px`,
          flex: `0 0 ${thumbnailWidth}px`,
          zIndex: index + 1,
          border: 0,
          borderRadius: 0,
          backgroundColor: "transparent",
          boxShadow: "0 5px 16px rgba(0, 0, 0, 0.2)",
          overflow: "visible",
          transformOrigin: "center center",
          opacity: 0,
          "--receipt-evidence-rotation": `${rotation}deg`,
          animationName: "receiptEvidenceIn",
          animationDuration: "320ms",
          animationTimingFunction: "ease-out",
          animationDelay: `${index * 55}ms`,
          animationFillMode: "forwards",
          animationPlayState: isVisible ? "running" : "paused",
        } as React.CSSProperties
      }
    >
      {hasErrored ? (
        <div
          aria-label={`${merchant} receipt unavailable`}
          style={{
            width: "100%",
            height: "100%",
            display: "grid",
            placeItems: "center",
            padding: "0.35rem",
            boxSizing: "border-box",
            color: "var(--text-color)",
            fontSize: "0.62rem",
            lineHeight: 1.25,
            textAlign: "center",
            opacity: 0.65,
          }}
        >
          Receipt unavailable
        </div>
      ) : (
        <Image
          src={currentUrl}
          alt={`${merchant} receipt`}
          width={sourceWidth}
          height={sourceHeight}
          sizes={`${Math.ceil(thumbnailWidth)}px`}
          loading={index < 3 ? "eager" : "lazy"}
          style={{
            width: "100%",
            height: "100%",
            display: "block",
            objectFit: "contain",
            objectPosition: "top center",
          }}
          onError={handleError}
        />
      )}
    </div>
  );
};

interface ReceiptEvidenceStackProps {
  receipts: ReceiptEvidence[];
  isVisible: boolean;
}

const ReceiptEvidenceStack: React.FC<ReceiptEvidenceStackProps> = ({
  receipts,
  isVisible,
}) => {
  const listRef = React.useRef<HTMLDivElement>(null);
  const [availableWidth, setAvailableWidth] = React.useState(374);

  React.useEffect(() => {
    const list = listRef.current;
    if (!list || typeof ResizeObserver === "undefined") return;

    const updateAvailableWidth = (width: number) => {
      setAvailableWidth((current) =>
        Math.abs(current - width) >= 0.5 ? width : current,
      );
    };
    updateAvailableWidth(list.getBoundingClientRect().width);

    const observer = new ResizeObserver(([entry]) => {
      if (entry) updateAvailableWidth(entry.contentRect.width);
    });
    observer.observe(list);
    return () => observer.disconnect();
  }, []);

  if (receipts.length === 0) return null;

  const visibleReceipts = receipts.slice(0, MAX_EVIDENCE_THUMBNAILS);
  const hiddenCount = receipts.length - visibleReceipts.length;
  const baseOverlapRatio = visibleReceipts.length > 5 ? 0.48 : 0.28;
  const receiptWidthRatios = visibleReceipts.map((receipt) => {
    const width = receipt.width > 0 ? receipt.width : 300;
    const height = receipt.height > 0 ? receipt.height : 900;
    return width / height;
  });
  const targetStackWidth = Math.max(
    1,
    availableWidth - EVIDENCE_STACK_EDGE_SAFETY,
  );
  const baseWidths = receiptWidthRatios.map(
    (ratio) => ratio * EVIDENCE_THUMBNAIL_HEIGHT,
  );
  const getStackBounds = (overlapRatio: number) => {
    if (baseWidths.length === 0) {
      return { positions: [] as number[], span: 0, min: 0 };
    }

    const positions = [0];
    let previousRight = baseWidths[0];
    let min = 0;
    let max = previousRight;
    for (let index = 1; index < baseWidths.length; index += 1) {
      const width = baseWidths[index];
      const left = previousRight - width * overlapRatio;
      const right = left + width;
      positions.push(left);
      min = Math.min(min, left);
      max = Math.max(max, right);
      previousRight = right;
    }
    return { positions, span: max - min, min };
  };
  let overlapRatio = baseOverlapRatio;
  let stackBounds = getStackBounds(overlapRatio);
  let smallestBounds = stackBounds;
  let smallestBoundsOverlap = overlapRatio;
  const OVERLAP_SEARCH_STEPS = 80;
  for (let step = 1; step <= OVERLAP_SEARCH_STEPS; step += 1) {
    const candidateOverlap =
      baseOverlapRatio +
      ((MAX_EVIDENCE_OVERLAP_RATIO - baseOverlapRatio) * step) /
        OVERLAP_SEARCH_STEPS;
    const candidateBounds = getStackBounds(candidateOverlap);
    if (candidateBounds.span < smallestBounds.span) {
      smallestBounds = candidateBounds;
      smallestBoundsOverlap = candidateOverlap;
    }
    if (candidateBounds.span <= targetStackWidth) {
      overlapRatio = candidateOverlap;
      stackBounds = candidateBounds;
      break;
    }
    if (step === OVERLAP_SEARCH_STEPS) {
      overlapRatio = smallestBoundsOverlap;
      stackBounds = smallestBounds;
    }
  }
  const thumbnailHeight =
    EVIDENCE_THUMBNAIL_HEIGHT *
    Math.min(1, targetStackWidth / Math.max(1, stackBounds.span));
  const stackScale = thumbnailHeight / EVIDENCE_THUMBNAIL_HEIGHT;
  const thumbnailLeftOffsets = stackBounds.positions.map(
    (position) => (position - stackBounds.min) * stackScale,
  );
  const stackWidth = stackBounds.span * stackScale;
  const receiptLabel = `${receipts.length} ${
    receipts.length === 1 ? "receipt" : "receipts"
  }`;

  return (
    <section
      role="region"
      aria-label="Receipt evidence"
      style={{
        width: "100%",
        paddingTop: "0.9rem",
        borderTop: "1px solid rgba(var(--text-color-rgb, 0, 0, 0), 0.14)",
        color: "var(--text-color)",
        flexShrink: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: "0.75rem",
          marginBottom: "0.65rem",
        }}
      >
        <strong style={{ fontSize: "0.78rem" }}>Evidence</strong>
        <span
          style={{
            fontFamily: "var(--font-mono, monospace)",
            fontSize: "0.68rem",
            opacity: 0.65,
          }}
        >
          <span>{receiptLabel}</span>
          {hiddenCount > 0 ? ` · showing ${visibleReceipts.length}` : null}
        </span>
      </div>

      <div
        ref={listRef}
        role="list"
        data-testid="receipt-evidence-list"
        data-available-width={availableWidth}
        data-overlap-ratio={overlapRatio}
        data-thumbnail-height={thumbnailHeight}
        aria-label={`${receiptLabel} used as evidence`}
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          width: "100%",
          maxWidth: "390px",
          height: "166px",
          margin: "0 auto",
          padding: "0.35rem 0.8rem 0.65rem",
          boxSizing: "border-box",
          overflow: "visible",
        }}
      >
        <div
          data-testid="receipt-evidence-stack"
          style={{
            position: "relative",
            width: `${stackWidth}px`,
            height: `${thumbnailHeight}px`,
            flex: "0 0 auto",
          }}
        >
          {visibleReceipts.map((receipt, index) => (
            <ReceiptEvidenceThumbnail
              key={receipt.imageId}
              receipt={receipt}
              index={index}
              isVisible={isVisible}
              thumbnailHeight={thumbnailHeight}
              leftOffset={thumbnailLeftOffsets[index] ?? 0}
            />
          ))}
        </div>
      </div>

      <style>{`
        @keyframes receiptEvidenceIn {
          from {
            opacity: 0;
            transform: rotate(var(--receipt-evidence-rotation, 0deg)) scale(0.98);
          }
          to {
            opacity: 1;
            transform: rotate(var(--receipt-evidence-rotation, 0deg)) translateY(0);
          }
        }

        @media (prefers-reduced-motion: reduce) {
          [aria-label="Receipt evidence"] [role="listitem"] {
            animation-duration: 1ms !important;
            animation-delay: 0ms !important;
          }
        }
      `}</style>
    </section>
  );
};

interface StructuredReceiptSummaryProps {
  receipts: StructuredReceipt[];
}

const StructuredReceiptSummary: React.FC<StructuredReceiptSummaryProps> = ({
  receipts,
}) => {
  if (receipts.length === 0) return null;

  const visibleReceipts = receipts.slice(0, MAX_STRUCTURED_RECEIPTS);
  const hiddenCount = receipts.length - visibleReceipts.length;

  return (
    <section aria-label="Structured receipts" style={{ width: "100%" }}>
      <strong
        style={{
          display: "block",
          marginBottom: "0.5rem",
          color: "var(--text-color)",
          fontSize: "0.78rem",
        }}
      >
        Structured receipts
      </strong>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: "0.5rem",
        }}
      >
        {visibleReceipts.map((receipt, receiptIndex) => (
          <div
            key={`${receipt.merchant}-${receiptIndex}`}
            style={{
              padding: "0.55rem 0.65rem",
              border: "1px solid rgba(var(--text-color-rgb, 0, 0, 0), 0.14)",
              borderRadius: "6px",
              backgroundColor: "var(--background-color)",
              color: "var(--text-color)",
            }}
          >
            <div style={{ fontSize: "0.72rem", fontWeight: 700 }}>
              {receipt.merchant || "Unknown merchant"}
            </div>
            {receipt.items.slice(0, 3).map((item, itemIndex) => (
              <div
                key={`${item.name}-${itemIndex}`}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: "0.5rem",
                  marginTop: "0.25rem",
                  fontSize: "0.68rem",
                  opacity: 0.72,
                }}
              >
                <span>{item.name}</span>
                <span>{CURRENCY_FORMATTER.format(item.amount)}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
      {hiddenCount > 0 ? (
        <div
          style={{
            marginTop: "0.4rem",
            color: "var(--text-color)",
            fontSize: "0.65rem",
            textAlign: "right",
            opacity: 0.58,
          }}
        >
          +{hiddenCount} more
        </div>
      ) : null}
    </section>
  );
};

// Structured receipt summaries (output of shape node)
const STRUCTURED_RECEIPTS: StructuredReceipt[] = [
  {
    merchant: "Neighborhood Market",
    items: [{ name: "WHOLE BEAN COFFEE", amount: 15.99 }],
  },
  {
    merchant: "La La Land",
    items: [
      { name: "Large Americano", amount: 4.7 },
      { name: "Cappuccino", amount: 5.3 },
    ],
  },
  { merchant: "Blue Bottle", items: [{ name: "Pour Over", amount: 5.25 }] },
  {
    merchant: "Le Pain Quotidien",
    items: [
      { name: "Iced Latte", amount: 6.25 },
      { name: "Iced Americano", amount: 5.0 },
    ],
  },
  { merchant: "Costco", items: [{ name: "KS ESPRESSO", amount: 15.89 }] },
];

// 5-node workflow trace: plan → agent ⟷ tools → shape → synthesize
const EXAMPLE_TRACE: TraceStep[] = [
  {
    type: "plan",
    content: "specific_item → semantic_hybrid",
    detail: "Classify question and choose retrieval strategy",
    durationMs: 1200,
  },
  {
    type: "agent",
    content: "Search for coffee using text and semantic",
    detail: "Deciding which tools to call based on classification",
    durationMs: 3400,
  },
  {
    type: "tools",
    content: 'search_receipts("COFFEE", "text") → 8 matches',
    detail: "Text search finds packaged coffee purchases",
    durationMs: 800,
  },
  {
    type: "agent",
    content: "Need semantic search for café drinks too",
    detail: "ReAct loop continues - more tools needed",
    durationMs: 2100,
  },
  {
    type: "tools",
    content: 'semantic_search("coffee espresso latte") → 6 matches',
    detail: "Finds café purchases: La La Land, Blue Bottle...",
    durationMs: 600,
  },
  {
    type: "agent",
    content: "Have enough context, ready to shape",
    detail: "10 unique receipts retrieved",
    durationMs: 1800,
  },
  {
    type: "shape",
    content: "10 receipts → 25 structured line items",
    detail: "Extract product names + amounts using word labels",
    durationMs: 4500,
    structuredData: STRUCTURED_RECEIPTS,
  },
  {
    type: "synthesize",
    content: "You spent $58.38 on coffee",
    detail: "5 receipts with coffee items identified",
    durationMs: 5200,
  },
];

const STEP_CONFIG: Record<
  StepType,
  {
    color: string;
    label: string;
    icon: string;
    node: string;
    description: string;
  }
> = {
  plan: {
    color: "var(--color-purple)",
    label: "Plan",
    icon: "📋",
    node: "1",
    description: "Classifying the question and picking a retrieval strategy",
  },
  agent: {
    color: "var(--color-blue)",
    label: "Agent",
    icon: "🤖",
    node: "2",
    description:
      "Reasoning about the question and deciding which tools to call",
  },
  tools: {
    color: "var(--color-green)",
    label: "Tools",
    icon: "🔧",
    node: "3",
    description: "Searching DynamoDB and ChromaDB for relevant receipts",
  },
  shape: {
    color: "var(--color-orange)",
    label: "Shape",
    icon: "📊",
    node: "4",
    description: "Filtering and structuring receipt summaries for synthesis",
  },
  synthesize: {
    color: "var(--color-red)",
    label: "Synthesize",
    icon: "✓",
    node: "5",
    description: "Composing the final answer with evidence citations",
  },
};

/** Default step duration (ms) when durationMs is not available */
const DEFAULT_STEP_MS = 1500;
/** Target total animation time (ms) for proportional scaling */
const TARGET_TOTAL_MS = 12000;
/** Minimum per-step duration to keep the animation readable */
const MIN_STEP_MS = 600;
/** Maximum per-step duration to prevent a single slow step dominating */
const MAX_STEP_MS = 3500;
const TIMELINE_LANE_HEIGHT = 24;

const QAAgentFlow: React.FC<QAAgentFlowProps> = ({
  autoPlay = true,
  questionData,
  onCycleComplete,
  children,
}) => {
  // threshold 0 (any pixel intersecting the rootMargin-expanded viewport),
  // NOT a fraction of the element: the tall intro stack makes a 10% area
  // threshold unreachable on mobile, which froze the flow on the intro and
  // never rendered the answer. "In view" should mean "the figure has entered
  // the viewport," independent of its total height.
  const [flowRef, isVisible] = useRevealInView({
    threshold: 0,
    rootMargin: "200px",
  });
  const [playbackTimeMs, setPlaybackTimeMs] = React.useState(-1);
  const [isPlaying, setIsPlaying] = React.useState(autoPlay);
  const [showAnswer, setShowAnswer] = React.useState(false);
  const [showDetails, setShowDetails] = React.useState(false);
  const motionScale = getReceiptMotionScale();

  // Use real data when available, fall back to example trace
  const trace = questionData?.trace ?? EXAMPLE_TRACE;
  const timelineLayout = React.useMemo(
    () => buildTimelineLayout(trace, DEFAULT_STEP_MS),
    [trace],
  );
  const playbackTimeline = React.useMemo(
    () =>
      buildTimelinePlayback(timelineLayout, {
        targetTotalMs: TARGET_TOTAL_MS,
        motionScale,
        minStepMs: MIN_STEP_MS,
        maxStepMs: MAX_STEP_MS,
      }),
    [timelineLayout, motionScale],
  );
  const synthesizeStep = trace.find((step) => step.type === "synthesize");
  const rawAnswer = synthesizeStep?.content ?? "";
  const answerText =
    rawAnswer
      .replace(/\n*#{0,3}\s*\*{0,2}Evidence\*{0,2}:?\**\s*[\s\S]*$/i, "")
      .replace(/\n*```(?:json)?\s*\[\s*[\s\S]*$/, "")
      .trim() || undefined;
  const answerSummary = answerText
    ? getAnswerSummaryMarkdown(answerText)
    : undefined;
  const evidenceReceipts = React.useMemo(
    () => deduplicateReceiptEvidence(trace),
    [trace],
  );
  const structuredReceipts = React.useMemo(
    () =>
      trace.find((step) => (step.structuredData?.length ?? 0) > 0)
        ?.structuredData ?? [],
    [trace],
  );
  const hasAnswerDetails =
    answerText !== answerSummary || structuredReceipts.length > 0;

  // Reset animation when question changes (use index to avoid stale-data issues on mobile)
  const questionIndex = questionData?.questionIndex ?? -1;
  React.useEffect(() => {
    setPlaybackTimeMs(-1);
    setShowAnswer(false);
    setShowDetails(false);
  }, [questionIndex]);

  React.useEffect(() => {
    setIsPlaying(autoPlay && isVisible);
  }, [autoPlay, isVisible]);

  const nextBoundaryMs = React.useMemo(
    () =>
      playbackTimeMs < 0
        ? 0
        : playbackTimeline.boundariesMs.find(
            (boundaryMs) => boundaryMs > playbackTimeMs,
          ),
    [playbackTimeMs, playbackTimeline.boundariesMs],
  );
  const activeStepIndices = React.useMemo(
    () =>
      playbackTimeMs < 0
        ? []
        : getActiveTimelineStepIndices(
            playbackTimeline.segments,
            playbackTimeMs,
          ),
    [playbackTimeMs, playbackTimeline.segments],
  );
  const activeStep = activeStepIndices[0] ?? -1;
  const visitedStepIndices = React.useMemo(
    () =>
      new Set(
        playbackTimeline.segments
          .filter((segment) => segment.startMs <= playbackTimeMs)
          .map((segment) => segment.stepIndex),
      ),
    [playbackTimeMs, playbackTimeline.segments],
  );
  const activePlaybackSegment = playbackTimeline.segments.find(
    (segment) => segment.stepIndex === activeStep,
  );
  const currentStepDuration =
    activePlaybackSegment?.durationMs ?? DEFAULT_STEP_MS;
  const activeParallelToolSteps = activeStepIndices.filter(
    (stepIndex) => trace[stepIndex]?.type === "tools",
  );

  // Advance through actual wall-clock boundaries. Overlapping spans are active
  // together, while legacy traces retain their clamped sequential playback.
  React.useEffect(() => {
    if (!isPlaying) return;

    if (showAnswer) {
      if (showDetails) return;

      const id = setTimeout(() => {
        // Reset animation before advancing so the next cycle always starts
        // fresh — even when consecutive null-data states produce the same
        // questionIndex (-1 → -1) and the questionIndex effect doesn't fire.
        setPlaybackTimeMs(-1);
        setShowAnswer(false);
        setShowDetails(false);
        onCycleComplete?.();
      }, 10000 * motionScale);
      return () => clearTimeout(id);
    }

    if (playbackTimeMs < 0) {
      const id = setTimeout(() => setPlaybackTimeMs(0), 400 * motionScale);
      return () => clearTimeout(id);
    }

    if (nextBoundaryMs == null) {
      setShowAnswer(true);
      return;
    }

    const id = setTimeout(
      () => setPlaybackTimeMs(nextBoundaryMs),
      Math.max(0, nextBoundaryMs - playbackTimeMs),
    );
    return () => clearTimeout(id);
  }, [
    isPlaying,
    playbackTimeMs,
    nextBoundaryMs,
    showAnswer,
    showDetails,
    onCycleComplete,
    motionScale,
  ]);

  const barTarget =
    playbackTimeMs < 0 || playbackTimeline.totalMs === 0
      ? 0
      : getTimelineRevealPercent(
          timelineLayout,
          playbackTimeline,
          nextBoundaryMs ?? playbackTimeline.totalMs,
        );
  const barDuration =
    playbackTimeMs < 0 || nextBoundaryMs == null
      ? 0
      : nextBoundaryMs - playbackTimeMs;

  const barSpring = useSpring({
    progress: barTarget,
    pause: !isPlaying,
    config: { duration: barDuration },
  });

  // Compute loop count: increments each time an agent step appears
  const loopCount = React.useMemo(() => {
    if (playbackTimeMs < 0) return 0;
    return trace.reduce(
      (count, step, stepIndex) =>
        step.type === "agent" && visitedStepIndices.has(stepIndex)
          ? count + 1
          : count,
      0,
    );
  }, [playbackTimeMs, trace, visitedStepIndices]);

  // Determine which node is currently active
  const activeType: StepType | null =
    activeStep >= 0 && activeStep < trace.length
      ? trace[activeStep].type
      : null;
  // Determine the loop phase bounds dynamically based on trace content
  const loopEndIdx =
    trace.length > 0
      ? trace.reduce(
          (last, s, i) => (s.type === "agent" || s.type === "tools" ? i : last),
          -1,
        )
      : 5;
  const inLoopPhase = activeStep >= 1 && activeStep <= loopEndIdx;

  return (
    <div
      ref={flowRef}
      style={
        {
          fontSize: "0.85rem",
          maxWidth: "1000px",
          margin: "0 auto",
          padding: "0.5rem",
          "--marquee-play-state": isVisible ? "running" : "paused",
        } as React.CSSProperties
      }
    >
      {/* Flame Graph Timeline + Node Diagram */}
      {(() => {
        const formatMs = (ms: number): string =>
          ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms.toFixed(1)}ms`;
        const parallelToolSegments = timelineLayout.segments.filter((segment) =>
          timelineLayout.parallelStepIndices.includes(segment.stepIndex),
        );

        // Deduplicate step types for the legend (e.g. multiple agent/tools steps)
        const uniqueTypes: StepType[] = [];
        for (const step of trace) {
          if (!uniqueTypes.includes(step.type)) uniqueTypes.push(step.type);
        }

        // Compute cumulative boundaries for pie-fill per legend entry
        const legendEntries = uniqueTypes.map((type) => {
          const cfg = STEP_CONFIG[type];
          const stepIndices = trace
            .map((s, i) => (s.type === type ? i : -1))
            .filter((i) => i >= 0);
          const visitedCount = stepIndices.filter((i) =>
            visitedStepIndices.has(i),
          ).length;
          const fillPercent =
            stepIndices.length > 0
              ? (visitedCount / stepIndices.length) * 100
              : 0;
          return { type, cfg, fillPercent };
        });

        // --- SVG node diagram constants ---
        const mainNodes: StepType[] = ["plan", "agent", "shape", "synthesize"];
        const S = 1.5;
        const nodeR = Math.round(16 * S);
        const spacing = Math.round(70 * S);
        const startX = nodeR + Math.round(10 * S);
        const mainXs = mainNodes.map((_, i) => startX + i * spacing);
        const svgW = mainXs[mainXs.length - 1] + nodeR + Math.round(10 * S);
        const toolsY = Math.round(95 * S);
        const svgH = toolsY + nodeR + Math.round(10 * S);
        const rowY = Math.round(22 * S);

        const agentX = mainXs[1];
        const toolsX = agentX;

        const forwardArrows: [number, number][] = [
          [0, 1],
          [1, 2],
          [2, 3],
        ];

        const isForwardArrowActive = (
          fromIdx: number,
          toIdx: number,
        ): boolean => {
          if (activeStep < 0) return false;
          const toType = mainNodes[toIdx];
          if (activeType !== toType) return false;
          const fromType = mainNodes[fromIdx];
          return activeStep > 0 && trace[activeStep - 1].type === fromType;
        };

        const ahLen = Math.round(7 * S);
        const ahHalf = Math.round(4 * S);
        const edgeGap = Math.round(4 * S);

        const angle = (40 * Math.PI) / 180;
        const edgeR = nodeR + edgeGap;
        const dx = Math.round(edgeR * Math.sin(angle));
        const dy = Math.round(edgeR * Math.cos(angle));
        const armLen = Math.round(20 * S);
        const tx = Math.cos(angle);
        const ty = Math.sin(angle);

        const rsx = agentX + dx,
          rsy = rowY + dy;
        const rTipX = agentX + dx,
          rTipY = toolsY - dy;
        const rex = rTipX + ahLen * tx,
          rey = rTipY - ahLen * ty;
        const downD = [
          `M ${rsx} ${rsy}`,
          `C ${rsx + armLen * tx} ${rsy + armLen * ty},`,
          `${rex + armLen * tx} ${rey - armLen * ty},`,
          `${rex} ${rey}`,
        ].join(" ");

        const lsx = agentX - dx,
          lsy = toolsY - dy;
        const lTipX = agentX - dx,
          lTipY = rowY + dy;
        const lex = lTipX - ahLen * tx,
          ley = lTipY + ahLen * ty;
        const upD = [
          `M ${lsx} ${lsy}`,
          `C ${lsx - armLen * tx} ${lsy - armLen * ty},`,
          `${lex - armLen * tx} ${ley + armLen * ty},`,
          `${lex} ${ley}`,
        ].join(" ");

        const downActive = activeType === "tools" && inLoopPhase;
        const upActive =
          activeType === "agent" && activeStep > 1 && inLoopPhase;
        const loopVisible = inLoopPhase || activeStep === loopEndIdx + 1;

        const badgeX = agentX;
        const badgeY = (rsy + rTipY) / 2;

        const downArrowAngle = 180 - 40;
        const upArrowAngle = -40;

        const renderArrowhead = (
          tipX: number,
          tipY: number,
          angleDeg: number,
          color: string,
        ) => (
          <polygon
            points={`0,0 ${-ahLen},${-ahHalf} ${-ahLen},${ahHalf}`}
            fill={color}
            transform={`translate(${tipX},${tipY}) rotate(${angleDeg})`}
          />
        );

        const circumference = Math.PI * nodeR;
        const fillDurationSec = (
          Math.max(1, currentStepDuration - 100) / 1000
        ).toFixed(2);

        const renderNode = (node: StepType, cx: number, cy: number) => {
          const cfg = STEP_CONFIG[node];
          const isNodeActive = activeType === node;
          const wasVisited = trace.some(
            (step, stepIndex) =>
              step.type === node && visitedStepIndices.has(stepIndex),
          );
          return (
            <g
              key={node}
              style={{ transition: "opacity 0.3s ease" }}
              opacity={isNodeActive ? 1 : wasVisited ? 0.85 : 0.3}
            >
              <circle
                cx={cx}
                cy={cy}
                r={nodeR}
                fill={
                  wasVisited && !isNodeActive
                    ? cfg.color
                    : "var(--code-background)"
                }
                stroke={cfg.color}
                strokeWidth={2 * S}
              />
              {isNodeActive && (
                <circle
                  key={`clock-${node}-${activeStep}`}
                  cx={cx}
                  cy={cy}
                  r={nodeR / 2}
                  fill="none"
                  stroke={cfg.color}
                  strokeWidth={nodeR}
                  strokeDasharray={circumference}
                  strokeDashoffset={circumference}
                  transform={`rotate(-90 ${cx} ${cy})`}
                  style={{
                    animationName: "clockFill",
                    animationDuration: `${fillDurationSec}s`,
                    animationTimingFunction: "linear",
                    animationFillMode: "forwards",
                    animationPlayState: isVisible ? "running" : "paused",
                  }}
                />
              )}
            </g>
          );
        };

        return (
          <div
            style={{
              width: "100%",
              padding: "1rem",
              backgroundColor: "var(--code-background)",
              borderRadius: "8px",
              fontSize: "0.85rem",
              boxSizing: "border-box",
              marginBottom: "0.75rem",
            }}
          >
            {/* Slot for marquee or other header content */}
            {children}

            {/* 5-Node SVG Flow Diagram */}
            <div style={{ textAlign: "center", marginBottom: "0.75rem" }}>
              <svg
                width="100%"
                height={svgH}
                viewBox={`0 0 ${svgW} ${svgH}`}
                style={{ maxWidth: `${svgW}px`, overflow: "visible" }}
              >
                <defs>
                  <style>{`
                    @keyframes clockFill {
                      from { stroke-dashoffset: ${circumference}; }
                      to   { stroke-dashoffset: 0; }
                    }
                  `}</style>
                </defs>
                {forwardArrows.map(([fromIdx, toIdx]) => {
                  const x1 = mainXs[fromIdx] + nodeR + edgeGap;
                  const tipX = mainXs[toIdx] - nodeR - edgeGap;
                  const x2 = tipX - ahLen;
                  const active = isForwardArrowActive(fromIdx, toIdx);
                  const color = active
                    ? STEP_CONFIG[mainNodes[toIdx]].color
                    : "var(--text-color)";
                  return (
                    <g
                      key={`fwd-${fromIdx}-${toIdx}`}
                      opacity={active ? 1 : 0.2}
                      style={{ transition: "opacity 0.3s ease" }}
                    >
                      <line
                        x1={x1}
                        y1={rowY}
                        x2={x2}
                        y2={rowY}
                        stroke={color}
                        strokeWidth={active ? 2.5 * S : 2 * S}
                      />
                      {renderArrowhead(tipX, rowY, 0, color)}
                    </g>
                  );
                })}

                <g
                  opacity={downActive ? 1 : loopVisible ? 0.35 : 0.15}
                  style={{ transition: "opacity 0.3s ease" }}
                >
                  <path
                    d={downD}
                    fill="none"
                    stroke={
                      downActive ? "var(--color-green)" : "var(--text-color)"
                    }
                    strokeWidth={downActive ? 2.5 * S : 2 * S}
                  />
                  {renderArrowhead(
                    rTipX,
                    rTipY,
                    downArrowAngle,
                    downActive ? "var(--color-green)" : "var(--text-color)",
                  )}
                </g>

                <g
                  opacity={upActive ? 1 : loopVisible ? 0.35 : 0.15}
                  style={{ transition: "opacity 0.3s ease" }}
                >
                  <path
                    d={upD}
                    fill="none"
                    stroke={
                      upActive ? "var(--color-blue)" : "var(--text-color)"
                    }
                    strokeWidth={upActive ? 2.5 * S : 2 * S}
                  />
                  {renderArrowhead(
                    lTipX,
                    lTipY,
                    upArrowAngle,
                    upActive ? "var(--color-blue)" : "var(--text-color)",
                  )}
                </g>

                {loopCount > 0 && (
                  <g
                    style={{ transition: "opacity 0.3s ease" }}
                    opacity={loopVisible ? 1 : 0}
                  >
                    <rect
                      x={badgeX - 14 * S}
                      y={badgeY - 9 * S}
                      width={28 * S}
                      height={18 * S}
                      rx={4 * S}
                      fill="var(--code-background)"
                      stroke="var(--color-green)"
                      strokeWidth={1 * S}
                      opacity={0.9}
                    />
                    <text
                      x={badgeX}
                      y={badgeY}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontSize={11 * S}
                      fontWeight={700}
                      fontFamily="var(--font-mono, monospace)"
                      fill="var(--color-green)"
                    >
                      {`×${loopCount}`}
                    </text>
                  </g>
                )}

                {mainNodes.map((node, idx) =>
                  renderNode(node, mainXs[idx], rowY),
                )}
                {renderNode("tools", toolsX, toolsY)}
              </svg>
            </div>

            {/* Active-phase narrative — shows what the agent is doing right now */}
            <div
              style={{
                minHeight: "1.6rem",
                marginBottom: "0.5rem",
                fontSize: "0.85rem",
                lineHeight: "1.4",
                color: "var(--text-color)",
                opacity: activeStep >= 0 && trace[activeStep] ? 1 : 0.4,
                transition: "opacity 0.3s ease",
                textAlign: "center",
              }}
            >
              {activeStep >= 0 && trace[activeStep] ? (
                <>
                  <strong
                    style={{ color: STEP_CONFIG[trace[activeStep].type].color }}
                  >
                    {STEP_CONFIG[trace[activeStep].type].label}:
                  </strong>{" "}
                  {STEP_CONFIG[trace[activeStep].type].description}
                </>
              ) : (
                <span style={{ fontStyle: "italic" }}>Ready</span>
              )}
            </div>

            {/* Wall-clock flame timeline — animated with spring */}
            {timelineLayout.hasParallelTools ? (
              <div style={{ marginBottom: "0.55rem" }}>
                <div
                  data-testid="parallel-tool-indicator"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    flexWrap: "wrap",
                    gap: "0.35rem 0.75rem",
                    marginBottom: "0.4rem",
                    color: "var(--color-green)",
                    fontFamily: "var(--font-mono, monospace)",
                    fontSize: "0.67rem",
                    fontWeight: 650,
                  }}
                >
                  <span>
                    <span aria-hidden="true">⇉</span> Parallel tool calls ·{" "}
                    {timelineLayout.laneCount} lanes
                  </span>
                  {activeParallelToolSteps.length > 1 ? (
                    <span data-testid="active-parallel-tools" role="status">
                      {activeParallelToolSteps.length} tools running together
                    </span>
                  ) : null}
                </div>
                <div
                  role="list"
                  aria-label="Parallel tool call details"
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      "repeat(auto-fit, minmax(min(100%, 220px), 1fr))",
                    gap: "0.35rem",
                  }}
                >
                  {parallelToolSegments.map((segment) => {
                    const step = trace[segment.stepIndex];
                    const isActive = activeStepIndices.includes(
                      segment.stepIndex,
                    );
                    return (
                      <div
                        key={`parallel-detail-${segment.stepIndex}`}
                        role="listitem"
                        style={{
                          display: "grid",
                          gridTemplateColumns: "auto minmax(0, 1fr) auto",
                          alignItems: "center",
                          gap: "0.45rem",
                          padding: "0.35rem 0.45rem",
                          border:
                            "1px solid rgba(var(--text-color-rgb, 0, 0, 0), 0.12)",
                          borderRadius: "4px",
                          color: "var(--text-color)",
                          backgroundColor: isActive
                            ? "rgba(var(--text-color-rgb, 0, 0, 0), 0.06)"
                            : "transparent",
                          fontFamily: "var(--font-mono, monospace)",
                          fontSize: "0.67rem",
                        }}
                      >
                        <span
                          aria-hidden="true"
                          style={{
                            width: "0.45rem",
                            height: "0.45rem",
                            borderRadius: "50%",
                            backgroundColor: STEP_CONFIG.tools.color,
                          }}
                        />
                        <span style={{ overflowWrap: "anywhere" }}>
                          {step.content}
                        </span>
                        <span style={{ opacity: 0.6 }}>
                          {formatMs(segment.durationMs)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}
            <div
              aria-label="QA trace wall-clock timeline"
              style={{
                position: "relative",
                width: "100%",
                height: `${timelineLayout.laneCount * TIMELINE_LANE_HEIGHT}px`,
                marginBottom: "0.75rem",
                borderRadius: "4px",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: "100%",
                  backgroundColor: "var(--background-color)",
                  borderRadius: "4px",
                  backgroundImage:
                    "linear-gradient(to bottom, transparent calc(100% - 1px), rgba(var(--text-color-rgb, 0, 0, 0), 0.1) calc(100% - 1px))",
                  backgroundSize: `100% ${TIMELINE_LANE_HEIGHT}px`,
                }}
              />
              <animated.div
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: "100%",
                  clipPath: barSpring.progress.to(
                    (p) => `inset(0 ${100 - p}% 0 0)`,
                  ),
                }}
              >
                {timelineLayout.segments.map((segment) => {
                  const step = trace[segment.stepIndex];
                  const cfg = STEP_CONFIG[step.type];
                  const widthPct =
                    (segment.durationMs / timelineLayout.totalMs) * 100;
                  const leftPct =
                    (segment.startMs / timelineLayout.totalMs) * 100;
                  const durationLabel = formatMs(segment.durationMs);
                  const compactDurationLabel = formatTimelineBarDuration(
                    segment.durationMs,
                  );
                  const showToolName = step.type === "tools" && widthPct >= 12;
                  const showDuration = !showToolName && widthPct >= 6;
                  const visibleLabel = showToolName
                    ? step.content
                    : showDuration
                      ? compactDurationLabel
                      : "";
                  return (
                    <div
                      key={`bar-${segment.stepIndex}-${step.type}`}
                      data-timeline-lane={segment.lane}
                      data-label-kind={
                        showToolName
                          ? "tool"
                          : showDuration
                            ? "duration"
                            : "none"
                      }
                      data-active={
                        activeStepIndices.includes(segment.stepIndex)
                          ? "true"
                          : "false"
                      }
                      aria-label={`${cfg.label}: ${step.content}; ${durationLabel}; starts at ${formatMs(segment.startMs)}`}
                      title={`${cfg.label}: ${step.content} · ${durationLabel} · starts at ${formatMs(segment.startMs)}`}
                      style={{
                        position: "absolute",
                        top: `${segment.lane * TIMELINE_LANE_HEIGHT}px`,
                        left: `${leftPct}%`,
                        width: `${widthPct}%`,
                        minWidth: "2px",
                        height: `${TIMELINE_LANE_HEIGHT - 2}px`,
                        backgroundColor: cfg.color,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        color: "rgba(255,255,255,0.95)",
                        fontSize: "clamp(0.58rem, 2.3vw, 0.7rem)",
                        fontFamily: "var(--font-mono, monospace)",
                        fontWeight: 600,
                        textShadow: "0 0 2px rgba(0,0,0,0.4)",
                        overflow: "hidden",
                        whiteSpace: "nowrap",
                        padding: visibleLabel
                          ? "0 clamp(0.125rem, 0.75vw, 0.25rem)"
                          : 0,
                        boxSizing: "border-box",
                      }}
                    >
                      {visibleLabel}
                    </div>
                  );
                })}
              </animated.div>
            </div>

            {/* Legend */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(140px, auto))",
                gap: "0.5rem 1.5rem",
                marginBottom: "0.75rem",
              }}
            >
              {legendEntries.map((entry, index) => {
                const prevComplete =
                  index === 0 || legendEntries[index - 1].fillPercent >= 100;
                const isComplete = entry.fillPercent >= 100;
                const isActive =
                  entry.fillPercent > 0 && entry.fillPercent < 100;
                const circleSize = 14;
                return (
                  <div
                    key={entry.type}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "0.5rem",
                      color: "var(--text-color)",
                      opacity: isComplete
                        ? 1
                        : isActive
                          ? 1
                          : prevComplete
                            ? 0.6
                            : 0.3,
                      transition: "opacity 0.15s ease",
                    }}
                  >
                    <svg
                      width={circleSize}
                      height={circleSize}
                      viewBox="0 0 14 14"
                      style={{ flexShrink: 0 }}
                    >
                      <circle
                        cx="7"
                        cy="7"
                        r="6"
                        fill={entry.fillPercent > 0 ? entry.cfg.color : "none"}
                        stroke={entry.cfg.color}
                        strokeWidth="1.5"
                        opacity={entry.fillPercent > 0 ? 1 : 0.5}
                      />
                    </svg>
                    <span style={{ fontSize: "0.85rem", fontWeight: 500 }}>
                      {entry.cfg.label}
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Stable answer frame: content changes never resize the page. */}
            <section
              data-testid="qa-result-frame"
              role="region"
              aria-label="QA answer result"
              aria-busy={!showAnswer}
              style={{
                height: "30rem",
                overflow: "hidden",
                overflowAnchor: "none",
                display: "flex",
                flexDirection: "column",
                marginTop: "0.75rem",
                marginBottom: "0.75rem",
                border: "1px solid rgba(var(--text-color-rgb, 0, 0, 0), 0.14)",
                borderRadius: "8px",
                backgroundColor: "var(--background-color)",
                color: "var(--text-color)",
                boxSizing: "border-box",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "0.75rem",
                  minHeight: "2.75rem",
                  padding: "0.65rem 0.8rem",
                  borderBottom:
                    "1px solid rgba(var(--text-color-rgb, 0, 0, 0), 0.12)",
                  boxSizing: "border-box",
                }}
              >
                <strong style={{ fontSize: "0.78rem" }}>Result</strong>
                <span
                  style={{
                    fontFamily: "var(--font-mono, monospace)",
                    fontSize: "0.67rem",
                    opacity: 0.62,
                  }}
                >
                  {showAnswer ? "Answer ready" : "Answer pending"}
                </span>
              </div>

              {showAnswer && answerText ? (
                <div
                  key={`answer-${questionIndex}`}
                  aria-live="polite"
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    flex: 1,
                    minHeight: 0,
                    width: "100%",
                    padding: "0.8rem",
                    boxSizing: "border-box",
                    animationName: "qaResultReveal",
                    animationDuration: "220ms",
                    animationTimingFunction: "ease-out",
                    animationFillMode: "both",
                  }}
                >
                  <div
                    id="qa-result-content"
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "1rem",
                      flex: 1,
                      minHeight: 0,
                      paddingRight: showDetails ? "0.35rem" : 0,
                      overflowX: "hidden",
                      overflowY: showDetails ? "auto" : "hidden",
                      overscrollBehavior: "contain",
                      fontSize: "0.9rem",
                      color: "var(--text-color)",
                    }}
                  >
                    <div
                      data-testid="qa-answer-summary"
                      style={{
                        minWidth: 0,
                        flexShrink: 0,
                        overflow: "visible",
                      }}
                    >
                      <ReactMarkdown>
                        {showDetails ? answerText : answerSummary}
                      </ReactMarkdown>
                    </div>
                    {showDetails ? (
                      <StructuredReceiptSummary receipts={structuredReceipts} />
                    ) : null}
                    <ReceiptEvidenceStack
                      receipts={evidenceReceipts}
                      isVisible={isVisible}
                    />
                  </div>

                  {hasAnswerDetails ? (
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "flex-end",
                        paddingTop: "0.65rem",
                        flexShrink: 0,
                      }}
                    >
                      <button
                        type="button"
                        data-testid="qa-result-details-toggle"
                        aria-expanded={showDetails}
                        aria-controls="qa-result-content"
                        onClick={() => setShowDetails((current) => !current)}
                        style={{
                          padding: "0.4rem 0.65rem",
                          border:
                            "1px solid rgba(var(--text-color-rgb, 0, 0, 0), 0.24)",
                          borderRadius: "999px",
                          backgroundColor: "transparent",
                          color: "var(--text-color)",
                          fontFamily: "inherit",
                          fontSize: "0.72rem",
                          fontWeight: 650,
                          cursor: "pointer",
                        }}
                      >
                        {showDetails ? "Show summary" : "View full answer"}
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : (
                <div
                  aria-live="polite"
                  style={{
                    display: "grid",
                    flex: 1,
                    placeItems: "center",
                    minHeight: 0,
                    padding: "1rem",
                    textAlign: "center",
                    opacity: 0.46,
                  }}
                >
                  <div>
                    <strong
                      style={{ display: "block", marginBottom: "0.35rem" }}
                    >
                      Tracing your question
                    </strong>
                    <span style={{ fontSize: "0.76rem" }}>
                      The completed answer and its receipt evidence will appear
                      here.
                    </span>
                  </div>
                </div>
              )}
              <style>{`
                @keyframes qaResultReveal {
                  from { opacity: 0; transform: translateY(4px); }
                  to { opacity: 1; transform: translateY(0); }
                }

                [data-testid="qa-answer-summary"] > :first-child {
                  margin-top: 0;
                }

                [data-testid="qa-answer-summary"] > :last-child {
                  margin-bottom: 0;
                }

                [data-testid="qa-answer-summary"] > :is(h1, h2, h3) {
                  margin-bottom: 0.35rem;
                  font-size: 1.15rem;
                  line-height: 1.25;
                }

                [data-testid="qa-answer-summary"] > p {
                  margin-top: 0;
                  font-size: 0.82rem;
                  line-height: 1.35;
                }

                @media (prefers-reduced-motion: reduce) {
                  [data-testid="qa-result-frame"] > [aria-live="polite"] {
                    animation-duration: 1ms !important;
                  }
                }
              `}</style>
            </section>
          </div>
        );
      })()}
    </div>
  );
};

export default QAAgentFlow;
