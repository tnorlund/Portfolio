import { animated, to, useSpring } from "@react-spring/web";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import { JourneyReceipt } from "../../../../types/api";
import { detectImageFormatSupport, getBestImageUrl, FormatSupport, ImageFormats } from "../../../../utils/imageFormat";
import styles from "./JourneyVisualization.module.css";

// Phase colors matching the pipeline
const PHASE_COLORS: Record<string, string> = {
  metadata_evaluation: "var(--color-blue)",
  currency_evaluation: "var(--color-green)",
  financial_validation: "var(--color-purple)",
  phase3_llm_review: "var(--color-orange)",
};

const PHASE_DISPLAY_NAMES: Record<string, string> = {
  metadata_evaluation: "Metadata",
  currency_evaluation: "Currency",
  financial_validation: "Financial",
  phase3_llm_review: "LLM Review",
};

const PHASE_ORDER = [
  "metadata_evaluation",
  "currency_evaluation",
  "financial_validation",
  "phase3_llm_review",
];

const DECISION_COLORS: Record<string, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  NEEDS_REVIEW: "var(--color-yellow)",
};

// Animation timing
const SCAN_DURATION_PER_PHASE = 1500;
const PHASE_OVERLAP = 200;
const HOLD_DURATION = 1000;
const TRANSITION_DURATION = 600;

// Layout constants (must match CSS)
const QUEUE_WIDTH = 120;
const QUEUE_ITEM_WIDTH = 100;
const QUEUE_ITEM_LEFT_INSET = 10;
const CENTER_COLUMN_WIDTH = 350;
const CENTER_COLUMN_HEIGHT = 500;
const QUEUE_HEIGHT = 400;
const COLUMN_GAP = 24;

// Build CDN keys from image_id and receipt_id
function buildCdnKeys(imageId: string, receiptId: number): ImageFormats {
  const paddedId = String(receiptId).padStart(5, "0");
  const base = `assets/${imageId}_RECEIPT_${paddedId}`;
  return {
    cdn_s3_key: `${base}.jpg`,
    cdn_webp_s3_key: `${base}.webp`,
    cdn_avif_s3_key: `${base}.avif`,
  };
}

// Stable random queue position based on receipt ID
const getQueuePosition = (receiptId: string) => {
  const hash = receiptId.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const random1 = Math.sin(hash * 9301 + 49297) % 1;
  const random2 = Math.sin(hash * 7919 + 12345) % 1;
  const rotation = Math.abs(random1) * 24 - 12;
  const leftOffset = Math.abs(random2) * 10 - 5;
  return { rotation, leftOffset };
};

// Compute phase stats from a receipt's journeys
function computePhaseStats(receipt: JourneyReceipt) {
  const phases: Record<string, { VALID: number; INVALID: number; NEEDS_REVIEW: number; total: number; durationMs: number }> = {};
  for (const p of PHASE_ORDER) {
    phases[p] = { VALID: 0, INVALID: 0, NEEDS_REVIEW: 0, total: 0, durationMs: 0 };
  }

  for (const journey of receipt.journeys) {
    for (const phase of journey.phases) {
      const stat = phases[phase.phase];
      if (!stat) continue;
      stat.total++;
      if (phase.decision === "VALID") stat.VALID++;
      else if (phase.decision === "INVALID") stat.INVALID++;
      else if (phase.decision === "NEEDS_REVIEW") stat.NEEDS_REVIEW++;

      // Compute duration from timestamps
      if (phase.start_time && phase.end_time) {
        const start = new Date(phase.start_time).getTime();
        const end = new Date(phase.end_time).getTime();
        if (!isNaN(start) && !isNaN(end)) {
          stat.durationMs = Math.max(stat.durationMs, end - start);
        }
      }
    }
  }
  return phases;
}

// --- Receipt Queue ---

interface ReceiptQueueProps {
  receipts: JourneyReceipt[];
  currentIndex: number;
  formatSupport: FormatSupport | null;
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
    const result: JourneyReceipt[] = [];
    for (let i = 1; i <= maxVisible; i++) {
      const idx = (currentIndex + i) % receipts.length;
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
        const cdnKeys = buildCdnKeys(receipt.image_id, receipt.receipt_id);
        const imageUrl = getBestImageUrl(cdnKeys, formatSupport, "thumbnail");
        const receiptId = `${receipt.image_id}_${receipt.receipt_id}`;
        const { rotation, leftOffset } = getQueuePosition(receiptId);

        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const stackOffset = Math.max(0, adjustedIdx) * STACK_GAP;
        const zIndex = maxVisible - idx;
        const isFlying = isTransitioning && idx === 0;
        const queueKey = `${receiptId}-queue-${idx}`;

        return (
          <div
            key={queueKey}
            className={`${styles.queuedReceipt} ${isFlying ? styles.flyingOut : ""}`}
            style={{
              top: `${stackOffset}px`,
              left: `${QUEUE_ITEM_LEFT_INSET + leftOffset}px`,
              transform: `rotate(${rotation}deg)`,
              zIndex,
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={imageUrl}
              alt={`Queued receipt ${idx + 1}`}
              style={{ width: "100%", height: "auto", display: "block" }}
            />
          </div>
        );
      })}
    </div>
  );
};

// --- Flying Receipt ---

interface FlyingReceiptProps {
  receipt: JourneyReceipt | null;
  formatSupport: FormatSupport | null;
  isFlying: boolean;
}

const FlyingReceipt: React.FC<FlyingReceiptProps> = ({
  receipt,
  formatSupport,
  isFlying,
}) => {
  const receiptId = receipt ? `${receipt.image_id}_${receipt.receipt_id}` : "";
  const { rotation, leftOffset } = getQueuePosition(receiptId);

  const imageUrl = useMemo(() => {
    if (!formatSupport || !receipt) return null;
    return getBestImageUrl(buildCdnKeys(receipt.image_id, receipt.receipt_id), formatSupport);
  }, [receipt, formatSupport]);

  // Image dimensions are unknown since API doesn't provide them.
  // Use a fixed display size that matches the center column constraints.
  const [imgDims, setImgDims] = useState<{ w: number; h: number }>({ w: 250, h: 400 });
  const imgRef = useRef<HTMLImageElement | null>(null);

  const onLoad = useCallback(() => {
    if (imgRef.current) {
      setImgDims({ w: imgRef.current.naturalWidth, h: imgRef.current.naturalHeight });
    }
  }, []);

  const aspectRatio = imgDims.w / Math.max(imgDims.h, 1);
  const maxHeight = 500;
  const maxWidth = 350;
  let displayHeight = Math.min(maxHeight, imgDims.h);
  let displayWidth = displayHeight * aspectRatio;
  if (displayWidth > maxWidth) {
    displayWidth = maxWidth;
    displayHeight = displayWidth / aspectRatio;
  }

  const distanceToQueueItemCenter =
    CENTER_COLUMN_WIDTH / 2 +
    COLUMN_GAP +
    (QUEUE_WIDTH - (QUEUE_ITEM_LEFT_INSET + leftOffset + QUEUE_ITEM_WIDTH / 2));
  const startX = -distanceToQueueItemCenter;

  const queueItemHeight = (imgDims.h / Math.max(imgDims.w, 1)) * QUEUE_ITEM_WIDTH;
  const queueItemCenterFromTop = (CENTER_COLUMN_HEIGHT - QUEUE_HEIGHT) / 2 + queueItemHeight / 2;
  const startY = queueItemCenterFromTop - CENTER_COLUMN_HEIGHT / 2;
  const startScale = QUEUE_ITEM_WIDTH / Math.max(displayWidth, 1);

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
            `translate(${xVal}px, ${yVal}px) scale(${scaleVal}) rotate(${rotateVal}deg)`
        ),
        marginLeft: -totalWidth / 2,
        marginTop: -totalHeight / 2,
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={imgRef}
        src={imageUrl}
        alt="Flying receipt"
        className={styles.flyingReceiptImage}
        style={{ width: displayWidth, height: displayHeight }}
        onLoad={onLoad}
      />
    </animated.div>
  );
};

// --- Receipt Viewer (center column) ---

interface ReceiptViewerProps {
  receipt: JourneyReceipt;
  phaseProgress: Record<string, number>; // 0-100 per phase
  formatSupport: FormatSupport | null;
}

const ReceiptViewer: React.FC<ReceiptViewerProps> = ({
  receipt,
  phaseProgress,
  formatSupport,
}) => {
  const cdnKeys = buildCdnKeys(receipt.image_id, receipt.receipt_id);
  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(cdnKeys, formatSupport);
  }, [cdnKeys, formatSupport]);

  // Track actual image dimensions for SVG viewBox
  const [imgDims, setImgDims] = useState<{ w: number; h: number } | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const onLoad = useCallback(() => {
    if (imgRef.current) {
      setImgDims({ w: imgRef.current.naturalWidth, h: imgRef.current.naturalHeight });
    }
  }, []);

  const filterId = `scanGlow_${receipt.image_id}_${receipt.receipt_id}`;

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  const hasActiveScan = PHASE_ORDER.some(
    (p) => (phaseProgress[p] ?? 0) > 0 && (phaseProgress[p] ?? 0) < 100
  );

  return (
    <div className={styles.receiptViewer}>
      <div className={styles.receiptImageWrapper}>
        <div className={styles.receiptImageInner}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            ref={imgRef}
            src={imageUrl}
            alt="Receipt"
            className={styles.receiptImage}
            onLoad={onLoad}
          />
          {imgDims && hasActiveScan && (
            <svg
              className={styles.svgOverlay}
              viewBox={`0 0 ${imgDims.w} ${imgDims.h}`}
              preserveAspectRatio="none"
            >
              <defs>
                <filter id={filterId} x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur stdDeviation="3" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>
              {PHASE_ORDER.map((phase) => {
                const progress = phaseProgress[phase] ?? 0;
                if (progress <= 0 || progress >= 100) return null;
                const y = (progress / 100) * imgDims.h;
                return (
                  <rect
                    key={phase}
                    x="0"
                    y={y}
                    width={imgDims.w}
                    height={Math.max(imgDims.h * 0.006, 3)}
                    fill={PHASE_COLORS[phase] || "var(--text-color)"}
                    filter={`url(#${filterId})`}
                  />
                );
              })}
            </svg>
          )}
        </div>
      </div>
    </div>
  );
};

// --- Phase Journey Panel (right column) ---

interface PhaseBarProps {
  name: string;
  color: string;
  progress: number;
  decisions: { VALID: number; INVALID: number; NEEDS_REVIEW: number; total: number };
  durationMs: number;
}

const PhaseBar: React.FC<PhaseBarProps> = ({ name, color, progress, decisions, durationMs }) => {
  const isActive = progress > 0 && progress < 100;
  const isComplete = progress >= 100;

  return (
    <div className={`${styles.phaseBar} ${isActive ? styles.active : ""} ${isComplete ? styles.complete : ""}`}>
      <div className={styles.phaseHeader}>
        <div className={styles.phaseIcon}>
          <svg width="16" height="16" viewBox="0 0 16 16">
            <circle cx="8" cy="8" r="6" fill="none" stroke={color} strokeWidth="1.5" opacity={0.3} />
            {progress > 0 && (
              <circle
                cx="8"
                cy="8"
                r="6"
                fill="none"
                stroke={color}
                strokeWidth="1.5"
                strokeDasharray={`${(progress / 100) * 37.7} 37.7`}
                strokeDashoffset="0"
                transform="rotate(-90 8 8)"
                opacity={isComplete ? 1 : 0.8}
              />
            )}
          </svg>
        </div>
        <span className={styles.phaseName}>{name}</span>
        <div className={styles.phaseMeta}>
          {isComplete && durationMs > 0 && (
            <span className={styles.phaseDuration}>
              {durationMs < 1000 ? `${durationMs.toFixed(0)}ms` : `${(durationMs / 1000).toFixed(1)}s`}
            </span>
          )}
          {decisions.total > 0 && (
            <div className={styles.decisionBadges}>
              {decisions.VALID > 0 && (
                <span className={styles.badge} style={{ backgroundColor: DECISION_COLORS.VALID }}>
                  {decisions.VALID}
                </span>
              )}
              {decisions.INVALID > 0 && (
                <span className={styles.badge} style={{ backgroundColor: DECISION_COLORS.INVALID }}>
                  {decisions.INVALID}
                </span>
              )}
              {decisions.NEEDS_REVIEW > 0 && (
                <span className={styles.badge} style={{ backgroundColor: DECISION_COLORS.NEEDS_REVIEW }}>
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
          style={{ width: `${progress}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
};

interface JourneyPanelProps {
  receipt: JourneyReceipt;
  phaseProgress: Record<string, number>;
}

const JourneyPanel: React.FC<JourneyPanelProps> = ({ receipt, phaseProgress }) => {
  const phaseStats = useMemo(() => computePhaseStats(receipt), [receipt]);
  const summary = receipt.summary;
  const conflictWords = useMemo(
    () => receipt.journeys.filter((j) => j.has_conflict).slice(0, 5),
    [receipt]
  );

  return (
    <div className={styles.journeyPanel}>
      <h4 className={styles.sectionTitle}>Pipeline Phases</h4>
      {PHASE_ORDER.map((phase) => {
        const stat = phaseStats[phase];
        const hasData = stat && stat.total > 0;
        return (
          <PhaseBar
            key={phase}
            name={PHASE_DISPLAY_NAMES[phase] || phase}
            color={PHASE_COLORS[phase] || "var(--text-color)"}
            progress={hasData ? (phaseProgress[phase] ?? 0) : 0}
            decisions={stat || { VALID: 0, INVALID: 0, NEEDS_REVIEW: 0, total: 0 }}
            durationMs={stat?.durationMs ?? 0}
          />
        );
      })}

      <div className={styles.summarySection}>
        <h4 className={styles.sectionTitle}>Summary</h4>
        <div className={styles.summaryStats}>
          <div className={styles.statItem}>
            <span className={styles.statLabel}>Words</span>
            <span className={styles.statValue}>
              {summary?.total_words_evaluated ?? receipt.journeys.length}
            </span>
          </div>
          <div className={styles.statItem}>
            <span className={styles.statLabel}>Conflicts</span>
            <span className={styles.statValue}>
              {summary?.words_with_conflicts ?? conflictWords.length}
            </span>
          </div>
        </div>
      </div>

      {conflictWords.length > 0 && (
        <div className={styles.conflictList}>
          <h4 className={styles.sectionTitle}>Conflicts</h4>
          {conflictWords.map((w) => (
            <div key={`${w.line_id}_${w.word_id}`} className={styles.conflictItem}>
              <span className={styles.conflictWord}>{w.word_text}</span>
              <span className={styles.conflictLabel}>{w.current_label}</span>
              <span
                className={styles.conflictOutcome}
                style={{ backgroundColor: DECISION_COLORS[w.final_outcome] || "var(--text-color)" }}
              >
                {w.final_outcome}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// --- Main Component ---

const JourneyVisualization: React.FC = () => {
  const { ref, inView } = useInView({ threshold: 0.3, triggerOnce: false });

  const [receipts, setReceipts] = useState<JourneyReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [phaseProgress, setPhaseProgress] = useState<Record<string, number>>({});
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [showFlyingReceipt, setShowFlyingReceipt] = useState(false);
  const [flyingReceipt, setFlyingReceipt] = useState<JourneyReceipt | null>(null);
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(null);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Control flying receipt visibility
  useEffect(() => {
    if (isTransitioning) {
      const next = receipts.length > 0 ? receipts[(currentIndex + 1) % receipts.length] : null;
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
        const response = await api.fetchLabelEvaluatorJourney(20);
        if (response?.receipts) {
          setReceipts(response.receipts);
        }
      } catch (err) {
        console.error("Failed to fetch journey data:", err);
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const currentReceipt = receipts[currentIndex];

  // Compute which phases have data for the current receipt
  const activePhases = useMemo(() => {
    if (!currentReceipt) return [];
    const stats = computePhaseStats(currentReceipt);
    return PHASE_ORDER.filter((p) => stats[p] && stats[p].total > 0);
  }, [currentReceipt]);

  // Animation loop
  useEffect(() => {
    if (!inView || receipts.length === 0) return;
    if (isAnimatingRef.current) return;
    isAnimatingRef.current = true;

    let receiptIdx = currentIndex;
    let startTime = performance.now();

    // Calculate timing for sequential phase scans with slight overlap
    const phaseDurations = PHASE_ORDER.map(() => SCAN_DURATION_PER_PHASE);
    const phaseStartTimes: number[] = [];
    let t = 0;
    for (let i = 0; i < PHASE_ORDER.length; i++) {
      phaseStartTimes.push(t);
      t += phaseDurations[i] - (i < PHASE_ORDER.length - 1 ? PHASE_OVERLAP : 0);
    }
    const allScansEnd = t;
    const holdEnd = allScansEnd + HOLD_DURATION;
    const totalCycle = holdEnd + TRANSITION_DURATION;

    let isInTransition = false;

    const animate = (time: number) => {
      const currentReceipts = receiptsRef.current;
      if (currentReceipts.length === 0) {
        isAnimatingRef.current = false;
        return;
      }

      const elapsed = time - startTime;

      if (elapsed < allScansEnd) {
        // Scanning phase - update each phase's progress
        const progress: Record<string, number> = {};
        for (let i = 0; i < PHASE_ORDER.length; i++) {
          const phaseStart = phaseStartTimes[i];
          const phaseDuration = phaseDurations[i];
          if (elapsed < phaseStart) {
            progress[PHASE_ORDER[i]] = 0;
          } else {
            const phaseElapsed = elapsed - phaseStart;
            progress[PHASE_ORDER[i]] = Math.min((phaseElapsed / phaseDuration) * 100, 100);
          }
        }
        setPhaseProgress(progress);
        setIsTransitioning(false);
      } else if (elapsed < holdEnd) {
        // Hold - all phases complete
        const complete: Record<string, number> = {};
        for (const p of PHASE_ORDER) complete[p] = 100;
        setPhaseProgress(complete);
        setIsTransitioning(false);
      } else if (elapsed < totalCycle) {
        // Transition
        if (!isInTransition) {
          isInTransition = true;
          setIsTransitioning(true);
        }
      } else {
        // Next receipt
        receiptIdx = (receiptIdx + 1) % currentReceipts.length;
        isInTransition = false;
        setCurrentIndex(receiptIdx);
        setPhaseProgress({});
        setIsTransitioning(false);
        startTime = time;
      }

      animationRef.current = requestAnimationFrame(animate);
    };

    animationRef.current = requestAnimationFrame(animate);

    return () => {
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
      isAnimatingRef.current = false;
    };
  }, [inView, receipts.length > 0, currentIndex]);

  if (loading) {
    return (
      <div ref={ref} className={styles.loading}>
        Loading journey data...
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
        No journey data available
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
          {/* Current receipt */}
          <div className={`${styles.receiptContainer} ${isTransitioning ? styles.fadeOut : ""}`}>
            <ReceiptViewer
              receipt={currentReceipt}
              phaseProgress={phaseProgress}
              formatSupport={formatSupport}
            />
          </div>

          {/* Flying receipt for desktop */}
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

          {/* Mobile crossfade */}
          {isTransitioning && nextReceipt && (
            <div className={`${styles.receiptContainer} ${styles.nextReceipt} ${styles.fadeIn}`}>
              <ReceiptViewer
                receipt={nextReceipt}
                phaseProgress={{}}
                formatSupport={formatSupport}
              />
            </div>
          )}
        </div>

        <JourneyPanel
          receipt={currentReceipt}
          phaseProgress={phaseProgress}
        />
      </div>
    </div>
  );
};

export default JourneyVisualization;
