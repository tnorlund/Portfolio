import React, { useEffect, useState, useMemo, useRef } from "react";
import { useInView } from "react-intersection-observer";
import { animated, useSpring, to } from "@react-spring/web";
import { api } from "../../../../services/api";
import {
  LabelValidationReceipt,
  LabelValidationWord,
} from "../../../../types/api";
import { detectImageFormatSupport, getBestImageUrl } from "../../../../utils/imageFormat";
import styles from "./LabelValidationVisualization.module.css";

// Animation state for two-tier validation
interface ValidationState {
  chromaProgress: number;  // 0-100
  llmProgress: number;     // 0-100
}

type Phase = "idle" | "scanning" | "complete";

// Validation tier colors - ChromaDB is fast/purple, LLM is slower/blue
const TIER_COLORS = {
  chroma: "var(--color-purple)",
  llm: "var(--color-blue)",
};

// Decision colors
const DECISION_COLORS: Record<string, string> = {
  VALID: "var(--color-green)",
  CORRECTED: "var(--color-orange)",
  NEEDS_REVIEW: "var(--color-yellow)",
};

// Animation timing (ms)
const TARGET_TOTAL_DURATION = 5000;  // 5 seconds for both tiers
const MIN_PHASE_DURATION = 800;
const HOLD_DURATION = 1000;
const TRANSITION_DURATION = 600;

// Generate stable random positions for queue items
const getQueuePosition = (receiptId: string) => {
  const hash = receiptId.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const random1 = Math.sin(hash * 9301 + 49297) % 1;
  const random2 = Math.sin(hash * 7919 + 12345) % 1;
  const rotation = (Math.abs(random1) * 24 - 12);
  const leftOffset = (Math.abs(random2) * 10 - 5);
  return { rotation, leftOffset };
};

// Receipt Queue Component
interface ReceiptQueueProps {
  receipts: LabelValidationReceipt[];
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

  const visibleReceipts = useMemo(() => {
    if (receipts.length === 0) return [];
    const result: LabelValidationReceipt[] = [];
    const totalReceipts = receipts.length;

    for (let i = 1; i <= maxVisible; i++) {
      const idx = (currentIndex + i) % totalReceipts;
      result.push(receipts[idx]);
    }
    return result;
  }, [receipts, currentIndex]);

  if (!formatSupport || visibleReceipts.length === 0) {
    return <div className={styles.receiptQueue} />;
  }

  const STACK_GAP = 20;

  return (
    <div className={styles.receiptQueue}>
      {visibleReceipts.map((receipt, idx) => {
        const imageUrl = getBestImageUrl(receipt, formatSupport, 'thumbnail');
        const { width, height } = receipt;
        const receiptId = `${receipt.image_id}_${receipt.receipt_id}`;
        const { rotation, leftOffset } = getQueuePosition(receiptId);

        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const stackOffset = Math.max(0, adjustedIdx) * STACK_GAP;
        const zIndex = maxVisible - idx;
        const isFlying = isTransitioning && idx === 0;

        const queueKey = `${receiptId}-queue-${idx}`;
        const centeredLeft = 90 + leftOffset;

        return (
          <div
            key={queueKey}
            className={`${styles.queuedReceipt} ${isFlying ? styles.flyingOut : ""}`}
            style={{
              top: `${stackOffset}px`,
              left: `${centeredLeft}px`,
              transform: `rotate(${rotation}deg)`,
              zIndex,
            }}
          >
            {imageUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={imageUrl}
                alt={`Queued receipt ${idx + 1}`}
                width={width}
                height={height}
                style={{
                  width: "100%",
                  height: "auto",
                  display: "block",
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
};

// Flying Receipt Component
interface FlyingReceiptProps {
  receipt: LabelValidationReceipt | null;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  isFlying: boolean;
  measuredContainerWidth?: number | null;
}

const FlyingReceipt: React.FC<FlyingReceiptProps> = ({
  receipt,
  formatSupport,
  isFlying,
  measuredContainerWidth,
}) => {
  const width = receipt?.width ?? 100;
  const height = receipt?.height ?? 150;
  const receiptId = receipt ? `${receipt.image_id}_${receipt.receipt_id}` : '';
  const { rotation, leftOffset } = getQueuePosition(receiptId);

  const imageUrl = useMemo(() => {
    if (!formatSupport || !receipt) return null;
    return getBestImageUrl(receipt, formatSupport);
  }, [receipt, formatSupport]);

  const aspectRatio = width / height;
  const maxHeight = 500;
  const maxWidth = measuredContainerWidth ?? 350;

  let displayHeight = Math.min(maxHeight, height);
  let displayWidth = displayHeight * aspectRatio;

  if (displayWidth > maxWidth) {
    displayWidth = maxWidth;
    displayHeight = displayWidth / aspectRatio;
  }

  const queueItemWidth = 100;
  const queueWidth = 280;
  const gap = 24;
  const centerColumnWidth = 350;
  const centerColumnHeight = 500;
  const queueHeight = 400;

  const queueItemLeft = 90 + leftOffset;
  const distanceToQueueItemCenter = (centerColumnWidth / 2) + gap + (queueWidth - (queueItemLeft + queueItemWidth / 2));
  const startX = -distanceToQueueItemCenter;

  const queueItemHeight = (height / width) * queueItemWidth;
  const queueItemCenterFromTop = ((centerColumnHeight - queueHeight) / 2) + (queueItemHeight / 2);
  const startY = queueItemCenterFromTop - (centerColumnHeight / 2);

  const startScale = queueItemWidth / displayWidth;

  const { x, y, scale, rotate } = useSpring({
    from: {
      x: startX,
      y: startY,
      scale: startScale,
      rotate: rotation,
    },
    to: {
      x: 0,
      y: 0,
      scale: 1,
      rotate: 0,
    },
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
            `translate(${xVal}px, ${yVal}px) scale(${scaleVal}) rotate(${rotateVal}deg)`
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
        style={{
          width: displayWidth,
          height: displayHeight,
        }}
      />
    </animated.div>
  );
};

// Validation tier bar component
interface TierBarProps {
  name: string;
  color: string;
  progress: number;
  isActive: boolean;
  isComplete: boolean;
  isWaiting?: boolean;
  durationMs?: number;
  decisions?: { VALID: number; CORRECTED: number; NEEDS_REVIEW: number };
  wordsCount?: number;
}

const TierBar: React.FC<TierBarProps> = ({
  name,
  color,
  progress,
  isActive,
  isComplete,
  isWaiting,
  durationMs,
  decisions,
  wordsCount,
}) => {
  const totalDecisions = decisions
    ? decisions.VALID + decisions.CORRECTED + decisions.NEEDS_REVIEW
    : 0;

  return (
    <div className={`${styles.tierBar} ${isActive ? styles.active : ""} ${isComplete ? styles.complete : ""} ${isWaiting ? styles.waiting : ""}`}>
      <div className={styles.tierHeader}>
        <div className={styles.tierNameWrapper}>
          <span className={styles.tierName}>{name}</span>
          {wordsCount !== undefined && wordsCount > 0 && (
            <span className={styles.tierWordCount}>{wordsCount} words</span>
          )}
        </div>
        <div className={styles.tierMeta}>
          {durationMs !== undefined && isComplete && (
            <span className={styles.durationBadge}>
              {durationMs < 1000 ? `${durationMs.toFixed(0)}ms` : `${(durationMs / 1000).toFixed(1)}s`}
            </span>
          )}
          {decisions && totalDecisions > 0 && (
            <div className={styles.decisionBadges}>
              {decisions.VALID > 0 && (
                <span
                  className={styles.badge}
                  style={{ backgroundColor: DECISION_COLORS.VALID }}
                >
                  {decisions.VALID}
                </span>
              )}
              {decisions.CORRECTED > 0 && (
                <span
                  className={styles.badge}
                  style={{ backgroundColor: DECISION_COLORS.CORRECTED }}
                >
                  {decisions.CORRECTED}
                </span>
              )}
              {decisions.NEEDS_REVIEW > 0 && (
                <span
                  className={styles.badge}
                  style={{ backgroundColor: DECISION_COLORS.NEEDS_REVIEW }}
                >
                  {decisions.NEEDS_REVIEW}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
      <div className={styles.progressTrack}>
        <div
          className={styles.progressFill}
          style={{
            width: `${progress}%`,
            backgroundColor: color,
          }}
        />
      </div>
    </div>
  );
};

// Receipt Viewer with word overlays
interface ReceiptViewerProps {
  receipt: LabelValidationReceipt;
  validationState: ValidationState;
  phase: Phase;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  onContainerMeasure?: (containerWidth: number) => void;
}

const ReceiptViewer: React.FC<ReceiptViewerProps> = ({
  receipt,
  validationState,
  phase,
  formatSupport,
  onContainerMeasure,
}) => {
  const { words, width, height } = receipt;
  const wrapperRef = useRef<HTMLDivElement>(null);

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport);
  }, [receipt, formatSupport]);

  const handleImageLoad = () => {
    if (wrapperRef.current && onContainerMeasure) {
      onContainerMeasure(wrapperRef.current.offsetWidth);
    }
  };

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  // Calculate which words should be revealed based on tier progress
  const getRevealedWords = (): LabelValidationWord[] => {
    return words.filter((word) => {
      const wordTopY = 1 - word.bbox.y - word.bbox.height;

      if (word.validation_source === "chroma") {
        return wordTopY <= (validationState.chromaProgress / 100);
      } else {
        // LLM words only revealed after ChromaDB completes
        return validationState.chromaProgress >= 100 && wordTopY <= (validationState.llmProgress / 100);
      }
    });
  };

  const revealedWords = getRevealedWords();

  // Calculate scan line positions
  const chromaY = (validationState.chromaProgress / 100) * height;
  const llmY = (validationState.llmProgress / 100) * height;
  const hasLLM = receipt.llm !== null;

  return (
    <div className={styles.receiptViewer}>
      <div ref={wrapperRef} className={styles.receiptImageWrapper}>
        <div className={styles.receiptImageInner}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt="Receipt"
            className={styles.receiptImage}
            width={width}
            height={height}
            onLoad={handleImageLoad}
          />
          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
          >
            <defs>
              <filter id="scanLineGlow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>

            {/* Word bounding boxes with decision indicators */}
            {revealedWords.map((word) => {
              const color = DECISION_COLORS[word.decision];
              const x = word.bbox.x * width;
              const y = (1 - word.bbox.y - word.bbox.height) * height;
              const w = word.bbox.width * width;
              const h = word.bbox.height * height;
              const centerX = x + w / 2;
              const centerY = y + h / 2;
              const circleRadius = Math.min(w, h) * 0.4;
              const iconSize = circleRadius * 0.6;
              const key = `${word.validation_source}_${word.line_id}_${word.word_id}`;

              return (
                <g key={key} className={styles.decisionIndicator}>
                  <rect
                    x={x}
                    y={y}
                    width={w}
                    height={h}
                    fill={color}
                    fillOpacity={0.3}
                    stroke={color}
                    strokeWidth={2}
                  />
                  <circle
                    cx={centerX}
                    cy={centerY}
                    r={circleRadius}
                    fill={color}
                  />
                  {/* Decision icons */}
                  {word.decision === 'VALID' && (
                    <path
                      d={`M ${centerX - iconSize * 0.8} ${centerY}
                          L ${centerX - iconSize * 0.2} ${centerY + iconSize * 0.6}
                          L ${centerX + iconSize * 0.8} ${centerY - iconSize * 0.5}`}
                      fill="none"
                      stroke="white"
                      strokeWidth={iconSize * 0.35}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  )}
                  {word.decision === 'CORRECTED' && (
                    <g>
                      {/* Pencil/edit icon */}
                      <path
                        d={`M ${centerX - iconSize * 0.5} ${centerY + iconSize * 0.5}
                            L ${centerX + iconSize * 0.3} ${centerY - iconSize * 0.3}
                            L ${centerX + iconSize * 0.6} ${centerY}
                            L ${centerX - iconSize * 0.2} ${centerY + iconSize * 0.8} Z`}
                        fill="white"
                      />
                    </g>
                  )}
                  {word.decision === 'NEEDS_REVIEW' && (
                    <g>
                      <circle
                        cx={centerX}
                        cy={centerY - iconSize * 0.35}
                        r={iconSize * 0.3}
                        fill="white"
                      />
                      <path
                        d={`M ${centerX - iconSize * 0.55} ${centerY + iconSize * 0.65}
                            Q ${centerX - iconSize * 0.55} ${centerY + iconSize * 0.1} ${centerX} ${centerY + iconSize * 0.1}
                            Q ${centerX + iconSize * 0.55} ${centerY + iconSize * 0.1} ${centerX + iconSize * 0.55} ${centerY + iconSize * 0.65}`}
                        fill="white"
                      />
                    </g>
                  )}
                </g>
              );
            })}

            {/* ChromaDB scan line */}
            {validationState.chromaProgress > 0 && validationState.chromaProgress < 100 && (
              <rect
                x="0"
                y={chromaY}
                width={width}
                height={Math.max(height * 0.006, 3)}
                fill={TIER_COLORS.chroma}
                filter="url(#scanLineGlow)"
              />
            )}

            {/* LLM scan line (only if LLM tier exists) */}
            {hasLLM && validationState.llmProgress > 0 && validationState.llmProgress < 100 && (
              <rect
                x="0"
                y={llmY}
                width={width}
                height={Math.max(height * 0.006, 3)}
                fill={TIER_COLORS.llm}
                filter="url(#scanLineGlow)"
              />
            )}
          </svg>
        </div>
      </div>
    </div>
  );
};

// Validation Legend Component
interface ValidationLegendProps {
  receipt: LabelValidationReceipt;
  validationState: ValidationState;
}

const ValidationLegend: React.FC<ValidationLegendProps> = ({
  receipt,
  validationState,
}) => {
  const { chroma, llm } = receipt;
  const hasLLM = llm !== null;
  const llmIsWaiting = hasLLM && validationState.chromaProgress < 100 && validationState.llmProgress === 0;

  return (
    <div className={styles.validationLegend}>
      <div className={styles.legendSection}>
        <h4 className={styles.sectionTitle}>
          <span className={styles.phaseNumber}>1</span>
          ChromaDB Consensus
        </h4>
        <TierBar
          name="ChromaDB"
          color={TIER_COLORS.chroma}
          progress={validationState.chromaProgress}
          isActive={validationState.chromaProgress > 0 && validationState.chromaProgress < 100}
          isComplete={validationState.chromaProgress >= 100}
          durationMs={validationState.chromaProgress >= 100 ? chroma.duration_seconds * 1000 : undefined}
          decisions={chroma.decisions}
          wordsCount={chroma.words_count}
        />
      </div>

      {hasLLM && (
        <div className={styles.legendSection}>
          <h4 className={styles.sectionTitle}>
            <span className={styles.phaseNumber}>2</span>
            LLM Fallback
          </h4>
          <TierBar
            name="LLM"
            color={TIER_COLORS.llm}
            progress={validationState.llmProgress}
            isActive={validationState.llmProgress > 0 && validationState.llmProgress < 100}
            isComplete={validationState.llmProgress >= 100}
            isWaiting={llmIsWaiting}
            durationMs={validationState.llmProgress >= 100 ? llm!.duration_seconds * 1000 : undefined}
            decisions={llm!.decisions}
            wordsCount={llm!.words_count}
          />
        </div>
      )}

      <div className={styles.summarySection}>
        <h4 className={styles.sectionTitle}>Legend</h4>
        <div className={styles.legendItems}>
          <div className={styles.legendItem}>
            <span className={styles.legendColor} style={{ backgroundColor: DECISION_COLORS.VALID }} />
            <span>Valid</span>
          </div>
          <div className={styles.legendItem}>
            <span className={styles.legendColor} style={{ backgroundColor: DECISION_COLORS.CORRECTED }} />
            <span>Corrected</span>
          </div>
          <div className={styles.legendItem}>
            <span className={styles.legendColor} style={{ backgroundColor: DECISION_COLORS.NEEDS_REVIEW }} />
            <span>Needs Review</span>
          </div>
        </div>
      </div>
    </div>
  );
};

const LabelValidationVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  const [receipts, setReceipts] = useState<LabelValidationReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [phase, setPhase] = useState<Phase>("idle");
  const [validationState, setValidationState] = useState<ValidationState>({
    chromaProgress: 0,
    llmProgress: 0,
  });
  const [formatSupport, setFormatSupport] = useState<{
    supportsWebP: boolean;
    supportsAVIF: boolean;
  } | null>(null);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [measuredContainerWidth, setMeasuredContainerWidth] = useState<number | null>(null);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Fetch visualization data
  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await api.fetchLabelValidationVisualization();
        if (response && response.receipts) {
          setReceipts(response.receipts);
        }
      } catch (err) {
        console.error("Failed to fetch label validation data:", err);
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const currentReceipt = receipts[currentIndex];

  // Animation loop
  useEffect(() => {
    if (!inView || receipts.length === 0) {
      return;
    }

    if (isAnimatingRef.current) {
      return;
    }
    isAnimatingRef.current = true;

    const receipt = receipts[currentIndex];
    const hasLLM = receipt.llm !== null;

    // Get actual durations from receipt data
    const rawChroma = receipt.chroma.duration_seconds || 0.5;
    const rawLLM = hasLLM ? (receipt.llm!.duration_seconds || 2) : 0;

    // Scale to target duration
    const totalRawDuration = rawChroma + rawLLM;
    const scaleFactor = TARGET_TOTAL_DURATION / (totalRawDuration * 1000);

    const chromaDuration = Math.max(rawChroma * 1000 * scaleFactor, MIN_PHASE_DURATION);
    const llmDuration = hasLLM
      ? Math.max(rawLLM * 1000 * scaleFactor, MIN_PHASE_DURATION)
      : 0;

    const llmStartTime = chromaDuration;
    const allTiersEnd = chromaDuration + llmDuration;
    const holdEnd = allTiersEnd + HOLD_DURATION;
    const totalCycle = holdEnd + TRANSITION_DURATION;

    let receiptIdx = currentIndex;
    let startTime = performance.now();

    setPhase("scanning");
    setIsTransitioning(false);
    setValidationState({
      chromaProgress: 0,
      llmProgress: 0,
    });

    let isInTransition = false;

    const animate = (time: number) => {
      const currentReceipts = receiptsRef.current;
      if (currentReceipts.length === 0) {
        isAnimatingRef.current = false;
        return;
      }

      const elapsed = time - startTime;

      if (elapsed < allTiersEnd) {
        // ChromaDB progress
        const chromaProgress = Math.min((elapsed / chromaDuration) * 100, 100);

        // LLM progress (starts after ChromaDB completes)
        let llmProgress = 0;
        if (hasLLM && elapsed >= llmStartTime) {
          const llmElapsed = elapsed - llmStartTime;
          llmProgress = Math.min((llmElapsed / llmDuration) * 100, 100);
        }

        setPhase("scanning");
        setIsTransitioning(false);
        setValidationState({
          chromaProgress,
          llmProgress,
        });
      } else if (elapsed < holdEnd) {
        setPhase("complete");
        setIsTransitioning(false);
        setValidationState({
          chromaProgress: 100,
          llmProgress: hasLLM ? 100 : 0,
        });
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
        setValidationState({
          chromaProgress: 0,
          llmProgress: 0,
        });
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
  }, [inView, receipts.length > 0, currentIndex]);

  if (loading) {
    return (
      <div ref={ref} className={styles.loading}>
        Loading label validation data...
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
        No label validation data available
      </div>
    );
  }

  const nextIndex = (currentIndex + 1) % receipts.length;
  const nextReceipt = receipts[nextIndex];

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
          <div className={`${styles.receiptContainer} ${isTransitioning ? styles.fadeOut : ''}`}>
            <ReceiptViewer
              receipt={currentReceipt}
              validationState={validationState}
              phase={phase}
              formatSupport={formatSupport}
              onContainerMeasure={setMeasuredContainerWidth}
            />
          </div>

          <div className={styles.flyingReceiptContainer}>
            {isTransitioning && nextReceipt && (
              <FlyingReceipt
                key={`flying-${nextReceipt.image_id}_${nextReceipt.receipt_id}`}
                receipt={nextReceipt}
                formatSupport={formatSupport}
                isFlying={isTransitioning}
                measuredContainerWidth={measuredContainerWidth}
              />
            )}
          </div>

          {isTransitioning && nextReceipt && (
            <div className={`${styles.receiptContainer} ${styles.nextReceipt} ${styles.fadeIn}`}>
              <ReceiptViewer
                receipt={nextReceipt}
                validationState={{ chromaProgress: 0, llmProgress: 0 }}
                phase="idle"
                formatSupport={formatSupport}
              />
            </div>
          )}
        </div>

        <ValidationLegend
          receipt={currentReceipt}
          validationState={validationState}
        />
      </div>
    </div>
  );
};

export default LabelValidationVisualization;
