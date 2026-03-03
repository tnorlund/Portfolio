import React, { useEffect, useState, useMemo, useRef } from "react";
import { useInView } from "react-intersection-observer";
import { useSpring, to, animated } from "@react-spring/web";
import { api } from "../../../../services/api";
import type { LabelEvaluatorReceipt } from "../../../../types/api";
import {
  detectImageFormatSupport,
  getBestImageUrl,
} from "../../../../utils/imageFormat";
import { DECISION_COLORS } from "./DecisionIcon";
import { TierBar } from "./TierBar";
import type {
  CarouselConfig,
  RevealedDecision,
  Phase,
  TierState,
} from "./types";
import styles from "./TwoTierFlow.module.css";

// Animation timing (ms)
const TARGET_TOTAL_DURATION = 5000;
const MIN_PHASE_DURATION = 800;
const HOLD_DURATION = 1000;
const TRANSITION_DURATION = 600;
// Tier 1 gets ~35% of total time, Tier 2 gets ~65%
const TIER1_FRACTION = 0.35;

// Layout constants
const QUEUE_WIDTH = 120;
const QUEUE_ITEM_WIDTH = 100;
const QUEUE_ITEM_LEFT_INSET = 10;
const CENTER_COLUMN_WIDTH = 350;
const CENTER_COLUMN_HEIGHT = 500;
const QUEUE_HEIGHT = 400;
const COLUMN_GAP = 24;

const getQueuePosition = (receiptId: string) => {
  const hash = receiptId
    .split("")
    .reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const random1 = Math.sin(hash * 9301 + 49297) % 1;
  const random2 = Math.sin(hash * 7919 + 12345) % 1;
  const rotation = Math.abs(random1) * 24 - 12;
  const leftOffset = Math.abs(random2) * 10 - 5;
  return { rotation, leftOffset };
};

// --- Sub-components ---

interface ReceiptQueueProps {
  receipts: LabelEvaluatorReceipt[];
  currentIndex: number;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  isTransitioning: boolean;
}

const ReceiptQueue: React.FC<ReceiptQueueProps> = ({
  receipts,
  currentIndex,
  formatSupport,
  isTransitioning,
}) => {
  const maxVisible = 6;
  const STACK_GAP = 20;

  const visibleReceipts = useMemo(() => {
    if (receipts.length === 0) return [];
    const result: LabelEvaluatorReceipt[] = [];
    for (let i = 1; i <= maxVisible; i++) {
      result.push(receipts[(currentIndex + i) % receipts.length]);
    }
    return result;
  }, [receipts, currentIndex]);

  if (!formatSupport || visibleReceipts.length === 0) {
    return <div className={styles.receiptQueue} />;
  }

  return (
    <div className={styles.receiptQueue}>
      {visibleReceipts.map((receipt, idx) => {
        const imageUrl = getBestImageUrl(receipt, formatSupport, "thumbnail");
        const receiptId = `${receipt.image_id}_${receipt.receipt_id}`;
        const { rotation, leftOffset } = getQueuePosition(receiptId);
        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const stackOffset = Math.max(0, adjustedIdx) * STACK_GAP;
        const zIndex = maxVisible - idx;
        const isFlying = isTransitioning && idx === 0;

        return (
          <div
            key={`${receiptId}-queue-${idx}`}
            className={`${styles.queuedReceipt} ${isFlying ? styles.flyingOut : ""}`}
            style={{
              top: `${stackOffset}px`,
              left: `${QUEUE_ITEM_LEFT_INSET + leftOffset}px`,
              transform: `rotate(${rotation}deg)`,
              zIndex,
            }}
          >
            {imageUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={imageUrl}
                alt={`Queued receipt ${idx + 1}`}
                width={receipt.width}
                height={receipt.height}
                style={{ width: "100%", height: "auto", display: "block" }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
};

interface FlyingReceiptProps {
  receipt: LabelEvaluatorReceipt | null;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  isFlying: boolean;
}

const FlyingReceipt: React.FC<FlyingReceiptProps> = ({
  receipt,
  formatSupport,
  isFlying,
}) => {
  const width = Math.max(receipt?.width ?? 100, 1);
  const height = Math.max(receipt?.height ?? 150, 1);
  const receiptId = receipt
    ? `${receipt.image_id}_${receipt.receipt_id}`
    : "";
  const { rotation, leftOffset } = getQueuePosition(receiptId);

  const imageUrl = useMemo(() => {
    if (!formatSupport || !receipt) return null;
    return getBestImageUrl(receipt, formatSupport);
  }, [receipt, formatSupport]);

  const aspectRatio = width / height;
  const maxHeight = 500;
  const maxWidth = 350;

  let displayHeight = Math.min(maxHeight, height);
  let displayWidth = displayHeight * aspectRatio;
  if (displayWidth > maxWidth) {
    displayWidth = maxWidth;
    displayHeight = displayWidth / aspectRatio;
  }

  const distanceToQueueItemCenter =
    CENTER_COLUMN_WIDTH / 2 +
    COLUMN_GAP +
    (QUEUE_WIDTH -
      (QUEUE_ITEM_LEFT_INSET + leftOffset + QUEUE_ITEM_WIDTH / 2));
  const startX = -distanceToQueueItemCenter;

  const queueItemHeight = (height / width) * QUEUE_ITEM_WIDTH;
  const queueItemCenterFromTop =
    (CENTER_COLUMN_HEIGHT - QUEUE_HEIGHT) / 2 + queueItemHeight / 2;
  const startY = queueItemCenterFromTop - CENTER_COLUMN_HEIGHT / 2;

  const startScale = QUEUE_ITEM_WIDTH / displayWidth;

  const { x, y, scale, rotate } = useSpring({
    from: { x: startX, y: startY, scale: startScale, rotate: rotation },
    to: { x: 0, y: 0, scale: 1, rotate: 0 },
    reset: true,
    config: { tension: 120, friction: 18 },
  });

  if (!receipt || !imageUrl || !isFlying) return null;

  const borderWidth = 1;
  const totalWidth = displayWidth + borderWidth * 2;
  const totalHeight = displayHeight + borderWidth * 2;

  return (
    <animated.div
      className={styles.flyingReceipt}
      style={{
        transform: to(
          [x, y, scale, rotate],
          (xVal, yVal, scaleVal, rotateVal) =>
            `translate(${xVal}px, ${yVal}px) scale(${scaleVal}) rotate(${rotateVal}deg)`,
        ),
        marginLeft: -totalWidth / 2,
        marginTop: -totalHeight / 2,
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={imageUrl}
        alt="Flying receipt"
        className={styles.flyingReceiptImage}
        style={{ width: displayWidth, height: displayHeight }}
      />
    </animated.div>
  );
};

interface ReceiptViewerProps {
  receipt: LabelEvaluatorReceipt;
  tierState: TierState;
  revealedDecisions: RevealedDecision[];
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  scanLineColors: [string, string];
}

const ReceiptViewer: React.FC<ReceiptViewerProps> = ({
  receipt,
  tierState,
  revealedDecisions,
  formatSupport,
  scanLineColors,
}) => {
  const { width, height } = receipt;

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport);
  }, [receipt, formatSupport]);

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  const tier1Y = (tierState.tier1 / 100) * height;
  const tier2Y = (tierState.tier2 / 100) * height;

  return (
    <div className={styles.receiptViewer}>
      <div className={styles.receiptImageWrapper}>
        <div className={styles.receiptImageInner}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt="Receipt"
            className={styles.receiptImage}
            width={width}
            height={height}
          />
          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
          >
            <defs>
              <filter
                id="scanLineGlow"
                x="-50%"
                y="-50%"
                width="200%"
                height="200%"
              >
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>

            {/* Decision bounding boxes */}
            {revealedDecisions.map((d) => {
              const color = DECISION_COLORS[d.decision];
              const bx = d.bbox.x * width;
              const by = (1 - d.bbox.y - d.bbox.height) * height;
              const bw = d.bbox.width * width;
              const bh = d.bbox.height * height;
              const cx = bx + bw / 2;
              const cy = by + bh / 2;
              const cr = Math.min(bw, bh) * 0.4;
              const iconSize = cr * 0.6;

              return (
                <g key={`indicator_${d.key}`} className={styles.decisionIndicator}>
                  <rect
                    x={bx}
                    y={by}
                    width={bw}
                    height={bh}
                    fill={color}
                    fillOpacity={0.3}
                    stroke={color}
                    strokeWidth={2}
                  />
                  <circle cx={cx} cy={cy} r={cr} fill={color} />
                  {d.decision === "VALID" && (
                    <path
                      d={`M ${cx - iconSize * 0.8} ${cy}
                          L ${cx - iconSize * 0.2} ${cy + iconSize * 0.6}
                          L ${cx + iconSize * 0.8} ${cy - iconSize * 0.5}`}
                      fill="none"
                      stroke="white"
                      strokeWidth={iconSize * 0.35}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  )}
                  {d.decision === "INVALID" && (
                    <g>
                      <line
                        x1={cx - iconSize * 0.5}
                        y1={cy - iconSize * 0.5}
                        x2={cx + iconSize * 0.5}
                        y2={cy + iconSize * 0.5}
                        stroke="white"
                        strokeWidth={iconSize * 0.35}
                        strokeLinecap="round"
                      />
                      <line
                        x1={cx + iconSize * 0.5}
                        y1={cy - iconSize * 0.5}
                        x2={cx - iconSize * 0.5}
                        y2={cy + iconSize * 0.5}
                        stroke="white"
                        strokeWidth={iconSize * 0.35}
                        strokeLinecap="round"
                      />
                    </g>
                  )}
                  {d.decision === "NEEDS_REVIEW" && (
                    <g>
                      <circle
                        cx={cx}
                        cy={cy - iconSize * 0.35}
                        r={iconSize * 0.3}
                        fill="white"
                      />
                      <path
                        d={`M ${cx - iconSize * 0.55} ${cy + iconSize * 0.65}
                            Q ${cx - iconSize * 0.55} ${cy + iconSize * 0.1} ${cx} ${cy + iconSize * 0.1}
                            Q ${cx + iconSize * 0.55} ${cy + iconSize * 0.1} ${cx + iconSize * 0.55} ${cy + iconSize * 0.65}`}
                        fill="white"
                      />
                    </g>
                  )}
                </g>
              );
            })}

            {/* Tier 1 scan line */}
            {tierState.tier1 > 0 && tierState.tier1 < 100 && (
              <rect
                x="0"
                y={tier1Y}
                width={width}
                height={Math.max(height * 0.006, 3)}
                fill={scanLineColors[0]}
                filter="url(#scanLineGlow)"
              />
            )}

            {/* Tier 2 scan line */}
            {tierState.tier2 > 0 && tierState.tier2 < 100 && (
              <rect
                x="0"
                y={tier2Y}
                width={width}
                height={Math.max(height * 0.006, 3)}
                fill={scanLineColors[1]}
                filter="url(#scanLineGlow)"
              />
            )}
          </svg>
        </div>
      </div>
    </div>
  );
};

// --- Main Carousel ---

interface EvaluatorCarouselProps {
  config: CarouselConfig;
}

export const EvaluatorCarousel: React.FC<EvaluatorCarouselProps> = ({
  config,
}) => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  const [receipts, setReceipts] = useState<LabelEvaluatorReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [phase, setPhase] = useState<Phase>("idle");
  const [tierState, setTierState] = useState<TierState>({
    tier1: 0,
    tier2: 0,
  });
  const [revealedDecisions, setRevealedDecisions] = useState<
    RevealedDecision[]
  >([]);
  const [formatSupport, setFormatSupport] = useState<{
    supportsWebP: boolean;
    supportsAVIF: boolean;
  } | null>(null);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [showFlyingReceipt, setShowFlyingReceipt] = useState(false);
  const [flyingReceipt, setFlyingReceipt] =
    useState<LabelEvaluatorReceipt | null>(null);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Control flying receipt visibility
  useEffect(() => {
    if (isTransitioning) {
      const next =
        receipts.length > 0
          ? receipts[(currentIndex + 1) % receipts.length]
          : null;
      setFlyingReceipt(next);
      setShowFlyingReceipt(true);
      return;
    }
    const timeout = setTimeout(() => {
      setShowFlyingReceipt(false);
      setFlyingReceipt(null);
    }, 50);
    return () => clearTimeout(timeout);
  }, [isTransitioning, currentIndex, receipts]);

  // Fetch data
  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await api.fetchLabelEvaluatorVisualization();
        if (response && response.receipts) {
          setReceipts(response.receipts);
        }
      } catch (err) {
        console.error("Failed to fetch evaluator data:", err);
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const currentReceipt = receipts[currentIndex];

  // Split decisions between tiers
  const splitDecisions = useMemo(() => {
    if (!currentReceipt) return { tier1: [], tier2: [] };
    const evaluation = config.getEvaluation(currentReceipt);
    const allDec = evaluation.all_decisions;
    const splitIdx = Math.ceil(allDec.length * TIER1_FRACTION);
    return {
      tier1: allDec.slice(0, splitIdx),
      tier2: allDec.slice(splitIdx),
    };
  }, [currentReceipt, config]);

  // Calculate revealed decisions based on tier progress
  useEffect(() => {
    if (!currentReceipt) return;

    const { words } = currentReceipt;
    const decisions: RevealedDecision[] = [];

    const isWordScanned = (
      lineId: number,
      wordId: number,
      progress: number,
    ) => {
      const word = words.find(
        (w) => w.line_id === lineId && w.word_id === wordId,
      );
      if (!word) return false;
      const wordTopY = 1 - word.bbox.y - word.bbox.height;
      return wordTopY <= progress / 100;
    };

    // Tier 1 decisions
    splitDecisions.tier1.forEach((d) => {
      if (isWordScanned(d.issue.line_id, d.issue.word_id, tierState.tier1)) {
        const word = words.find(
          (w) => w.line_id === d.issue.line_id && w.word_id === d.issue.word_id,
        );
        if (word) {
          decisions.push({
            key: `t1_${d.issue.line_id}_${d.issue.word_id}`,
            tier: 1,
            decision: d.llm_review.decision as
              | "VALID"
              | "INVALID"
              | "NEEDS_REVIEW",
            wordText: d.issue.word_text,
            lineId: d.issue.line_id,
            wordId: d.issue.word_id,
            bbox: word.bbox,
          });
        }
      }
    });

    // Tier 2 decisions
    splitDecisions.tier2.forEach((d) => {
      if (isWordScanned(d.issue.line_id, d.issue.word_id, tierState.tier2)) {
        const word = words.find(
          (w) => w.line_id === d.issue.line_id && w.word_id === d.issue.word_id,
        );
        if (word) {
          decisions.push({
            key: `t2_${d.issue.line_id}_${d.issue.word_id}`,
            tier: 2,
            decision: d.llm_review.decision as
              | "VALID"
              | "INVALID"
              | "NEEDS_REVIEW",
            wordText: d.issue.word_text,
            lineId: d.issue.line_id,
            wordId: d.issue.word_id,
            bbox: word.bbox,
          });
        }
      }
    });

    setRevealedDecisions(decisions);
  }, [currentReceipt, tierState, splitDecisions]);

  // Animation loop
  useEffect(() => {
    if (!inView || receipts.length === 0) return;
    if (isAnimatingRef.current) return;
    isAnimatingRef.current = true;

    const receipt = receipts[currentIndex];
    const evaluation = config.getEvaluation(receipt);
    const rawDuration = evaluation.duration_seconds || 1;

    const scaleFactor = TARGET_TOTAL_DURATION / (rawDuration * 1000);
    const totalAnimDuration = Math.max(
      rawDuration * 1000 * scaleFactor,
      MIN_PHASE_DURATION * 2,
    );

    const tier1Duration = Math.max(
      totalAnimDuration * TIER1_FRACTION,
      MIN_PHASE_DURATION,
    );
    const tier2Duration = Math.max(
      totalAnimDuration * (1 - TIER1_FRACTION),
      MIN_PHASE_DURATION,
    );
    const tier2Start = tier1Duration;
    const allTiersEnd = tier2Start + tier2Duration;
    const holdEnd = allTiersEnd + HOLD_DURATION;
    const totalCycle = holdEnd + TRANSITION_DURATION;

    let receiptIdx = currentIndex;
    let startTime = performance.now();

    setPhase("scanning");
    setIsTransitioning(false);
    setTierState({ tier1: 0, tier2: 0 });

    let isInTransition = false;

    const animate = (time: number) => {
      const currentReceipts = receiptsRef.current;
      if (currentReceipts.length === 0) {
        isAnimatingRef.current = false;
        return;
      }

      const elapsed = time - startTime;

      if (elapsed < allTiersEnd) {
        const tier1Progress = Math.min((elapsed / tier1Duration) * 100, 100);
        let tier2Progress = 0;
        if (elapsed >= tier2Start) {
          tier2Progress = Math.min(
            ((elapsed - tier2Start) / tier2Duration) * 100,
            100,
          );
        }

        setPhase("scanning");
        setIsTransitioning(false);
        setTierState({ tier1: tier1Progress, tier2: tier2Progress });
      } else if (elapsed < holdEnd) {
        setPhase("complete");
        setIsTransitioning(false);
        setTierState({ tier1: 100, tier2: 100 });
      } else if (elapsed < totalCycle) {
        if (!isInTransition) {
          isInTransition = true;
          setIsTransitioning(true);
        }
      } else {
        receiptIdx = (receiptIdx + 1) % currentReceipts.length;
        isInTransition = false;
        setCurrentIndex(receiptIdx);
        setPhase("scanning");
        setIsTransitioning(false);
        setTierState({ tier1: 0, tier2: 0 });
        setRevealedDecisions([]);
        startTime = time;
      }

      animationRef.current = requestAnimationFrame(animate);
    };

    animationRef.current = requestAnimationFrame(animate);

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
      isAnimatingRef.current = false;
    };
  }, [inView, receipts.length > 0, currentIndex, config]);

  if (loading) {
    return (
      <div ref={ref} className={styles.loading}>
        Loading evaluation data...
      </div>
    );
  }

  if (error) {
    return (
      <div ref={ref} className={styles.error}>
        Error: {error}
      </div>
    );
  }

  if (receipts.length === 0) {
    return (
      <div ref={ref} className={styles.loading}>
        No evaluation data available
      </div>
    );
  }

  const nextIndex = (currentIndex + 1) % receipts.length;
  const nextReceipt = receipts[nextIndex];
  const evaluation = config.getEvaluation(currentReceipt);
  const nextEvaluation = nextReceipt
    ? config.getEvaluation(nextReceipt)
    : null;

  const tier1Decisions = revealedDecisions.filter((d) => d.tier === 1);
  const tier2Decisions = revealedDecisions.filter((d) => d.tier === 2);
  const tier1Total = splitDecisions.tier1.length;
  const tier2Total = splitDecisions.tier2.length;

  // Next receipt split for tally placeholders
  const nextAllDec = nextEvaluation?.all_decisions ?? [];
  const nextSplitIdx = Math.ceil(nextAllDec.length * TIER1_FRACTION);
  const nextTier1Total = nextSplitIdx;
  const nextTier2Total = nextAllDec.length - nextSplitIdx;

  return (
    <div ref={ref} className={styles.container}>
      <div className={styles.mainWrapper}>
        <ReceiptQueue
          receipts={receipts}
          currentIndex={currentIndex}
          formatSupport={formatSupport}
          isTransitioning={isTransitioning}
        />

        <div className={styles.centerColumn}>
          <div
            className={`${styles.receiptContainer} ${isTransitioning ? styles.fadeOut : ""}`}
          >
            <ReceiptViewer
              receipt={currentReceipt}
              tierState={tierState}
              revealedDecisions={revealedDecisions}
              formatSupport={formatSupport}
              scanLineColors={config.scanLineColors}
            />
          </div>

          <div className={styles.flyingReceiptContainer}>
            {showFlyingReceipt && flyingReceipt && (
              <FlyingReceipt
                key={`flying-${flyingReceipt.image_id}_${flyingReceipt.receipt_id}`}
                receipt={flyingReceipt}
                formatSupport={formatSupport}
                isFlying={showFlyingReceipt}
              />
            )}
          </div>

          {isTransitioning && nextReceipt && (
            <div
              className={`${styles.receiptContainer} ${styles.nextReceipt} ${styles.fadeIn}`}
            >
              <ReceiptViewer
                receipt={nextReceipt}
                tierState={{ tier1: 0, tier2: 0 }}
                revealedDecisions={[]}
                formatSupport={formatSupport}
                scanLineColors={config.scanLineColors}
              />
            </div>
          )}
        </div>

        <div className={styles.scannerLegend}>
          <TierBar
            config={config.tier1}
            progress={tierState.tier1}
            isWaiting={false}
            isComplete={tierState.tier1 >= 100}
            durationMs={
              tierState.tier1 >= 100
                ? evaluation.duration_seconds * 1000 * TIER1_FRACTION
                : undefined
            }
            decisions={tier1Decisions}
            totalDecisions={tier1Total}
            isTransitioning={isTransitioning}
            nextTotalDecisions={nextTier1Total}
          />
          <TierBar
            config={config.tier2}
            progress={tierState.tier2}
            isWaiting={tierState.tier1 < 100 && tierState.tier2 === 0}
            isComplete={tierState.tier2 >= 100}
            durationMs={
              tierState.tier2 >= 100
                ? evaluation.duration_seconds * 1000 * (1 - TIER1_FRACTION)
                : undefined
            }
            decisions={tier2Decisions}
            totalDecisions={tier2Total}
            isTransitioning={isTransitioning}
            nextTotalDecisions={nextTier2Total}
            showPlaceholders={tierState.tier1 >= 100}
          />

          <div className={styles.summarySection}>
            <div className={styles.summaryStats}>
              <div className={styles.statItem}>
                <span className={styles.statLabel}>Decisions</span>
                <span className={styles.statValue}>
                  {evaluation.all_decisions.length}
                </span>
              </div>
              <div className={styles.statItem}>
                <span className={styles.statLabel}>Words</span>
                <span className={styles.statValue}>
                  {currentReceipt.words.length}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
