import React, { useEffect, useState, useMemo, useRef } from "react";
import { useInView } from "react-intersection-observer";
import { animated } from "@react-spring/web";
import { api } from "../../../../services/api";
import {
  LabelValidationReceipt,
  LabelValidationWord,
} from "../../../../types/api";
import { getBestImageUrl } from "../../../../utils/imageFormat";
import { ReceiptFlowShell } from "../ReceiptFlow/ReceiptFlowShell";
import {
  getQueuePosition,
  getVisibleQueueIndices,
} from "../ReceiptFlow/receiptFlowUtils";
import { ImageFormatSupport } from "../ReceiptFlow/types";
import { useImageFormatSupport } from "../ReceiptFlow/useImageFormatSupport";
import { FlyingReceipt } from "../ReceiptFlow/FlyingReceipt";
import { useFlyingReceipt } from "../ReceiptFlow/useFlyingReceipt";
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
  INVALID: "var(--color-orange)",
  NEEDS_REVIEW: "var(--color-yellow)",
};

// Revealed decision for tally animation
interface RevealedDecision {
  id: string;
  decision: "VALID" | "INVALID" | "NEEDS_REVIEW";
}

// Decision Icon Component - shows checkmark/X/person based on decision
const DecisionIcon: React.FC<{ decision: RevealedDecision }> = ({ decision }) => {
  const bgColor = DECISION_COLORS[decision.decision];
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" className={styles.decisionIconSvg}>
      <circle cx="7" cy="7" r="6" fill={bgColor} />
      {decision.decision === 'VALID' && (
        <path
          d="M4 7 L6 9.5 L10 5"
          fill="none"
          stroke="white"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
      {decision.decision === 'INVALID' && (
        <g>
          <line x1="4.5" y1="4.5" x2="9.5" y2="9.5" stroke="white" strokeWidth="1.8" strokeLinecap="round" />
          <line x1="9.5" y1="4.5" x2="4.5" y2="9.5" stroke="white" strokeWidth="1.8" strokeLinecap="round" />
        </g>
      )}
      {decision.decision === 'NEEDS_REVIEW' && (
        <g>
          {/* Person silhouette */}
          <circle cx="7" cy="5" r="1.5" fill="white" />
          <path
            d="M4 11 Q4 8 7 8 Q10 8 10 11"
            fill="white"
          />
        </g>
      )}
    </svg>
  );
};

// Empty icon placeholder
const EmptyIcon: React.FC = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" className={styles.emptyIconSvg}>
    <circle cx="7" cy="7" r="6" fill="var(--text-color)" fillOpacity="0.15" />
  </svg>
);

// Animation timing (ms)
const TARGET_TOTAL_DURATION = 5000;  // 5 seconds for both tiers
const MIN_PHASE_DURATION = 800;
const HOLD_DURATION = 1000;
const TRANSITION_DURATION = 600;

// Generate SVG path for a pie slice from 12 o'clock, filling clockwise
const getPieSlicePath = (progress: number, cx: number, cy: number, r: number): string => {
  if (progress <= 0) return '';
  if (progress >= 100) return `M ${cx} ${cy} m -${r} 0 a ${r} ${r} 0 1 0 ${r * 2} 0 a ${r} ${r} 0 1 0 -${r * 2} 0`;

  const angle = (progress / 100) * 2 * Math.PI;
  // Start at 12 o'clock (-π/2)
  const startAngle = -Math.PI / 2;
  const endAngle = startAngle + angle;

  const x1 = cx + r * Math.cos(startAngle);
  const y1 = cy + r * Math.sin(startAngle);
  const x2 = cx + r * Math.cos(endAngle);
  const y2 = cy + r * Math.sin(endAngle);

  const largeArcFlag = progress > 50 ? 1 : 0;

  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArcFlag} 1 ${x2} ${y2} Z`;
};

// Receipt Queue Component
interface ReceiptQueueProps {
  receipts: LabelValidationReceipt[];
  currentIndex: number;
  formatSupport: ImageFormatSupport | null;
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
    return getVisibleQueueIndices(receipts.length, currentIndex, maxVisible, true).map(
      (idx) => receipts[idx]
    );
  }, [receipts, currentIndex]);

  if (!formatSupport || visibleReceipts.length === 0) {
    return <div className={styles.receiptQueue} />;
  }

  const STACK_GAP = 20;

  return (
    <div className={styles.receiptQueue} data-rf-queue>
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

// Decision Tally Component - shows animated icons for each decision type
interface DecisionTallyProps {
  label: string;
  total: number;
  revealed: RevealedDecision[];
  decisionType: "VALID" | "INVALID" | "NEEDS_REVIEW";
}

const DecisionTally: React.FC<DecisionTallyProps> = ({
  label,
  total,
  revealed,
  decisionType,
}) => {
  const filteredRevealed = revealed.filter(r => r.decision === decisionType);
  const maxDisplay = 12; // Limit icons displayed to prevent overflow
  const displayTotal = Math.min(total, maxDisplay);
  const hasMore = total > maxDisplay;

  if (total === 0) return null;

  return (
    <div className={styles.decisionTally}>
      <span className={styles.tallyLabel}>{label}</span>
      <div className={styles.tallyIcons}>
        {Array.from({ length: displayTotal }).map((_, idx) => {
          const revealedAtIndex = filteredRevealed[idx];
          if (revealedAtIndex) {
            return (
              <animated.span
                key={`revealed-${revealedAtIndex.id}`}
                className={styles.tallyIcon}
                style={{ opacity: 1, transform: 'scale(1)' }}
              >
                <DecisionIcon decision={revealedAtIndex} />
              </animated.span>
            );
          }

          return (
            <span key={`empty-${idx}`} className={styles.tallyIcon}>
              <EmptyIcon />
            </span>
          );
        })}
        {hasMore && (
          <span className={styles.tallyMore}>+{total - maxDisplay}</span>
        )}
      </div>
    </div>
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
  decisions?: { VALID: number; INVALID: number; NEEDS_REVIEW: number; UNKNOWN?: number };
  wordsCount?: number;
  revealedDecisions?: RevealedDecision[];
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
  revealedDecisions = [],
}) => {
  const invalidCount = decisions
    ? decisions.INVALID ?? (decisions as Record<string, number>).CORRECTED ?? 0
    : 0;

  return (
    <div className={`${styles.tierBar} ${isActive ? styles.active : ""} ${isComplete ? styles.complete : ""} ${isWaiting ? styles.waiting : ""}`}>
      <div className={styles.tierHeader}>
        {/* Progress circle that fills like a clock */}
        <div className={styles.progressCircleWrapper}>
          <svg width="20" height="20" viewBox="0 0 20 20" className={styles.progressCircle}>
            {/* Background circle (unfilled outline) */}
            <circle
              cx="10"
              cy="10"
              r="8"
              fill="none"
              stroke={color}
              strokeWidth="2"
              opacity={0.3}
            />
            {/* Pie slice fill - grows clockwise from 12 o'clock */}
            {progress > 0 && (
              <path
                d={getPieSlicePath(progress, 10, 10, 8)}
                fill={color}
                opacity={isComplete ? 1 : 0.8}
              />
            )}
          </svg>
        </div>
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
          {isWaiting && (
            <span className={styles.waitingBadge}>waiting</span>
          )}
        </div>
      </div>
      {/* Decision tallies with icons */}
      {decisions && (
        <div className={styles.decisionTallies}>
          <DecisionTally
            label="Valid"
            total={decisions.VALID}
            revealed={revealedDecisions}
            decisionType="VALID"
          />
          <DecisionTally
            label="Invalid"
            total={invalidCount}
            revealed={revealedDecisions}
            decisionType="INVALID"
          />
          <DecisionTally
            label="Review"
            total={decisions.NEEDS_REVIEW}
            revealed={revealedDecisions}
            decisionType="NEEDS_REVIEW"
          />
        </div>
      )}
    </div>
  );
};

// Receipt Viewer with word overlays
interface ReceiptViewerProps {
  receipt: LabelValidationReceipt;
  validationState: ValidationState;
  phase: Phase;
  formatSupport: ImageFormatSupport | null;
}

const ReceiptViewer: React.FC<ReceiptViewerProps> = ({
  receipt,
  validationState,
  phase,
  formatSupport,
}) => {
  const { words, width, height } = receipt;

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport);
  }, [receipt, formatSupport]);

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  // Calculate which words should be revealed based on tier progress
  const getRevealedWords = (): LabelValidationWord[] => {
    return words.filter((word) => {
      if (!word.validation_source || !word.decision) {
        return false;
      }
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
              const rawDecision = word.decision ?? "";
              const decision =
                rawDecision === "CORRECTED"
                  ? "INVALID"
                  : rawDecision;
              const color = DECISION_COLORS[decision];
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
                  {decision === 'VALID' && (
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
                  {decision === 'INVALID' && (
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
                  {decision === 'NEEDS_REVIEW' && (
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
  const { chroma, llm, words } = receipt;
  const hasLLM = llm !== null;
  const llmIsWaiting = hasLLM && validationState.chromaProgress < 100 && validationState.llmProgress === 0;

  // Calculate revealed decisions based on validation progress
  const getRevealedDecisions = (tier: "chroma" | "llm"): RevealedDecision[] => {
    const progress = tier === "chroma" ? validationState.chromaProgress : validationState.llmProgress;

    // LLM words only revealed after ChromaDB completes
    if (tier === "llm" && validationState.chromaProgress < 100) {
      return [];
    }

    return words
      .filter((word) => {
        if (word.validation_source !== tier || !word.decision) return false;
        const wordTopY = 1 - word.bbox.y - word.bbox.height;
        return wordTopY <= (progress / 100);
      })
      .map((word) => {
        const rawDecision = word.decision ?? "";
        const normalizedDecision =
          rawDecision === "CORRECTED"
            ? "INVALID"
            : rawDecision as "VALID" | "INVALID" | "NEEDS_REVIEW";
        return {
          id: `${word.validation_source}_${word.line_id}_${word.word_id}`,
          decision: normalizedDecision,
        };
      });
  };

  const chromaRevealed = getRevealedDecisions("chroma");
  const llmRevealed = getRevealedDecisions("llm");

  return (
    <div className={styles.validationLegend}>
      <TierBar
        name="ChromaDB"
        color={TIER_COLORS.chroma}
        progress={validationState.chromaProgress}
        isActive={validationState.chromaProgress > 0 && validationState.chromaProgress < 100}
        isComplete={validationState.chromaProgress >= 100}
        durationMs={validationState.chromaProgress >= 100 ? chroma.duration_seconds * 1000 : undefined}
        decisions={chroma.decisions}
        wordsCount={chroma.words_count}
        revealedDecisions={chromaRevealed}
      />

      {hasLLM && (
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
          revealedDecisions={llmRevealed}
        />
      )}
    </div>
  );
};

// Inner component - only mounted when receipts are loaded
interface LabelValidationInnerProps {
  observerRef: (node?: Element | null) => void;
  inView: boolean;
  receipts: LabelValidationReceipt[];
  formatSupport: ImageFormatSupport | null;
}

const LabelValidationInner: React.FC<LabelValidationInnerProps> = ({
  observerRef,
  inView,
  receipts,
  formatSupport,
}) => {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [phase, setPhase] = useState<Phase>("idle");
  const [validationState, setValidationState] = useState<ValidationState>({
    chromaProgress: 0,
    llmProgress: 0,
  });
  const [isTransitioning, setIsTransitioning] = useState(false);

  const { flyingItem, showFlying } = useFlyingReceipt(
    isTransitioning,
    receipts,
    currentIndex,
  );

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

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
  }, [inView, receipts.length, currentIndex]);

  const nextIndex = (currentIndex + 1) % receipts.length;
  const nextReceipt = receipts[nextIndex];

  const flyingElement = useMemo(() => {
    if (!showFlying || !flyingItem || !formatSupport) return null;
    const fUrl = getBestImageUrl(flyingItem, formatSupport);
    if (!fUrl) return null;
    const ar = flyingItem.width / flyingItem.height;
    let dh = Math.min(500, flyingItem.height);
    let dw = dh * ar;
    if (dw > 350) { dw = 350; dh = dw / ar; }
    return (
      <FlyingReceipt
        key={`flying-${flyingItem.image_id}_${flyingItem.receipt_id}`}
        imageUrl={fUrl}
        displayWidth={dw}
        displayHeight={dh}
        receiptId={`${flyingItem.image_id}_${flyingItem.receipt_id}`}
        queueItemLeftInset={90}
      />
    );
  }, [showFlying, flyingItem, formatSupport]);

  return (
    <div ref={observerRef} className={styles.container}>
      <ReceiptFlowShell
        layoutVars={
          {
            "--rf-queue-width": "280px",
            "--rf-queue-height": "400px",
            "--rf-center-max-width": "350px",
            "--rf-center-height": "500px",
            "--rf-mobile-center-height": "400px",
            "--rf-mobile-center-height-sm": "320px",
            "--rf-gap": "1.5rem",
          } as React.CSSProperties
        }
        isTransitioning={isTransitioning}
        queue={
          <ReceiptQueue
            receipts={receipts}
            currentIndex={currentIndex}
            formatSupport={formatSupport}
            isTransitioning={isTransitioning}
          />
        }
        center={
          <ReceiptViewer
            receipt={currentReceipt}
            validationState={validationState}
            phase={phase}
            formatSupport={formatSupport}
          />
        }
        flying={flyingElement}
        next={
          isTransitioning && nextReceipt ? (
            <ReceiptViewer
              receipt={nextReceipt}
              validationState={{ chromaProgress: 0, llmProgress: 0 }}
              phase="idle"
              formatSupport={formatSupport}
            />
          ) : null
        }
        legend={
          <ValidationLegend
          receipt={currentReceipt}
          validationState={validationState}
          />
        }
      />
    </div>
  );
};

// Outer component - handles data fetching and loading guards
const LabelValidationVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  const [receipts, setReceipts] = useState<LabelValidationReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const formatSupport = useImageFormatSupport();

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

  return (
    <LabelValidationInner
      observerRef={ref}
      inView={inView}
      receipts={receipts}
      formatSupport={formatSupport}
    />
  );
};

export default LabelValidationVisualization;
