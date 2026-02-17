import React, { useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  WithinReceiptVerificationReceipt,
  WithinReceiptWordDecision,
} from "../../../../types/api";
import { getBestImageUrl, getJpegFallbackUrl, usePreloadReceiptImages } from "../../../../utils/imageFormat";
import { ReceiptFlowShell } from "../ReceiptFlow/ReceiptFlowShell";
import { ReceiptFlowLoadingShell } from "../ReceiptFlow/ReceiptFlowLoadingShell";
import {
  getQueuePosition,
  getVisibleQueueIndices,
} from "../ReceiptFlow/receiptFlowUtils";
import { ImageFormatSupport } from "../ReceiptFlow/types";
import { useImageFormatSupport } from "../ReceiptFlow/useImageFormatSupport";
import { FlyingReceipt } from "../ReceiptFlow/FlyingReceipt";
import { useFlyingReceipt } from "../ReceiptFlow/useFlyingReceipt";
import styles from "./WithinReceiptVerification.module.css";

// ---------------------------------------------------------------------------
// Types & Constants
// ---------------------------------------------------------------------------

type PassName = "place" | "format";
type Phase = "idle" | "scanning" | "complete";

interface PassState {
  place: number;    // 0-100
  format: number;   // 0-100
}

interface RevealedDecision {
  key: string;
  pass: PassName;
  currentLabel: string;
  wordText: string;
  lineId: number;
  wordId: number;
  bbox: { x: number; y: number; width: number; height: number };
}

// Label colors — matches BetweenReceiptVisualization convention
const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  ADDRESS_LINE: "var(--color-red)",
  PHONE_NUMBER: "var(--color-orange)",
  WEBSITE: "var(--color-purple)",
  STORE_HOURS: "var(--color-orange)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  PAYMENT_METHOD: "var(--color-orange)",
  COUPON: "var(--color-teal, #2dd4bf)",
  LOYALTY_ID: "var(--color-teal, #2dd4bf)",
  O: "var(--text-color)",
};


// Animation timing
const PLACE_DURATION = 2000;
const FORMAT_DURATION = 1500;
const HOLD_DURATION = 1000;
const TRANSITION_DURATION = 600;

const LAYOUT_VARS = {
  "--rf-queue-width": "120px",
  "--rf-queue-height": "400px",
  "--rf-center-max-width": "350px",
  "--rf-center-height": "500px",
  "--rf-mobile-center-height": "400px",
  "--rf-mobile-center-height-sm": "320px",
  "--rf-gap": "1.5rem",
  "--rf-align-items": "flex-start",
} as React.CSSProperties;


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Receipt Queue
// ---------------------------------------------------------------------------

interface ReceiptQueueProps {
  receipts: WithinReceiptVerificationReceipt[];
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

        return (
          <div
            key={queueKey}
            className={`${styles.queuedReceipt} ${isFlying ? styles.flyingOut : ""}`}
            style={{
              top: `${stackOffset}px`,
              left: `${10 + leftOffset}px`,
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
                style={{ width: "100%", height: "auto", display: "block" }}
                onError={(e) => {
                  const fallback = getJpegFallbackUrl(receipt);
                  if (e.currentTarget.src !== fallback) {
                    e.currentTarget.src = fallback;
                  }
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
};


// ---------------------------------------------------------------------------
// Receipt Viewer (SVG overlay with scan lines + label-colored bounding boxes)
// ---------------------------------------------------------------------------

interface ReceiptViewerProps {
  receipt: WithinReceiptVerificationReceipt;
  passState: PassState;
  phase: Phase;
  revealedDecisions: RevealedDecision[];
  formatSupport: ImageFormatSupport | null;
}

const ReceiptViewer: React.FC<ReceiptViewerProps> = ({
  receipt,
  passState,
  phase,
  revealedDecisions,
  formatSupport,
}) => {
  const [dims, setDims] = useState<{ width: number; height: number } | null>(null);
  const width = dims?.width ?? receipt.width;
  const height = dims?.height ?? receipt.height;

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport);
  }, [receipt, formatSupport]);

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  const placeY = (passState.place / 100) * height;
  const formatY = (passState.format / 100) * height;

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
            onLoad={(e) => {
              const img = e.currentTarget;
              if (img.naturalWidth > 0) {
                setDims({ width: img.naturalWidth, height: img.naturalHeight });
              }
            }}
            onError={(e) => {
              const fallback = getJpegFallbackUrl(receipt);
              if (e.currentTarget.src !== fallback) {
                e.currentTarget.src = fallback;
              }
            }}
          />
          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
          >
            {/* Label-colored bounding boxes */}
            {revealedDecisions.map((d) => {
              const color = LABEL_COLORS[d.currentLabel] || "var(--text-color)";
              const x = d.bbox.x * width;
              const y = (1 - d.bbox.y - d.bbox.height) * height;
              const w = d.bbox.width * width;
              const h = d.bbox.height * height;

              return (
                <rect
                  key={d.key}
                  x={x} y={y} width={w} height={h}
                  fill={color} fillOpacity={0.15}
                  stroke={color} strokeWidth={1.5} strokeOpacity={0.5}
                />
              );
            })}

            {/* Scan lines — subtle, matching BetweenReceipt style */}
            {passState.place > 0 && passState.place < 100 && (
              <rect
                x="0" y={placeY} width={width}
                height={1}
                fill="var(--text-color)"
                opacity={0.3}
              />
            )}
            {passState.format > 0 && passState.format < 100 && (
              <rect
                x="0" y={formatY} width={width}
                height={1}
                fill="var(--text-color)"
                opacity={0.3}
              />
            )}
          </svg>
        </div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Right Panel Cards
// ---------------------------------------------------------------------------

const DecisionTally: React.FC<{ summary: { total: number; valid: number; invalid: number; needs_review: number } }> = ({ summary }) => {
  if (summary.total === 0) return null;
  return (
    <div className={styles.decisionTally}>
      {summary.valid > 0 && (
        <div className={styles.tallyItem}>
          <span className={styles.tallyDot} style={{ background: "var(--color-green)" }} />
          <span className={styles.tallyCount}>{summary.valid}</span>
        </div>
      )}
      {summary.invalid > 0 && (
        <div className={styles.tallyItem}>
          <span className={styles.tallyDot} style={{ background: "var(--color-red)" }} />
          <span className={styles.tallyCount}>{summary.invalid}</span>
        </div>
      )}
      {summary.needs_review > 0 && (
        <div className={styles.tallyItem}>
          <span className={styles.tallyDot} style={{ background: "var(--color-yellow)" }} />
          <span className={styles.tallyCount}>{summary.needs_review}</span>
        </div>
      )}
    </div>
  );
};

const PlaceCard: React.FC<{ receipt: WithinReceiptVerificationReceipt }> = ({ receipt }) => {
  const { place_validation } = receipt;
  const place = place_validation.place;

  return (
    <div className={styles.card}>
      <h4 className={styles.cardTitle}>Google Places</h4>
      <div className={styles.cardBody}>
        {place ? (
          <>
            <div className={styles.placeField}>
              <span className={styles.placeLabel}>Name</span>
              <span className={styles.placeValue}>{place.merchant_name || <span className={styles.placeValue + ' ' + styles.missing}>Unknown</span>}</span>
            </div>
            <div className={styles.placeField}>
              <span className={styles.placeLabel}>Address</span>
              <span className={place.formatted_address ? styles.placeValue : `${styles.placeValue} ${styles.missing}`}>
                {place.formatted_address || "Not found"}
              </span>
            </div>
            {place.phone_number && (
              <div className={styles.placeField}>
                <span className={styles.placeLabel}>Phone</span>
                <span className={styles.placeValue}>{place.phone_number}</span>
              </div>
            )}
            {place.website && (
              <div className={styles.placeField}>
                <span className={styles.placeLabel}>Website</span>
                <span className={styles.placeValue}>{place.website}</span>
              </div>
            )}
            {place.confidence != null && (
              <div className={styles.confidenceGauge}>
                <div className={styles.gaugeTrack}>
                  <div
                    className={styles.gaugeFill}
                    style={{
                      width: `${(place.confidence * 100)}%`,
                      backgroundColor: place.confidence > 0.7 ? "var(--color-green)"
                        : place.confidence > 0.4 ? "var(--color-yellow)"
                        : "var(--color-red)",
                    }}
                  />
                </div>
                <span className={styles.gaugeLabel}>{(place.confidence * 100).toFixed(0)}%</span>
              </div>
            )}
          </>
        ) : (
          <span className={`${styles.placeValue} ${styles.missing}`}>No Places data</span>
        )}
        <DecisionTally summary={place_validation.summary} />
      </div>
    </div>
  );
};

const FormatPatternsCard: React.FC<{ receipt: WithinReceiptVerificationReceipt }> = ({ receipt }) => {
  const { format_validation } = receipt;

  // Group decisions by label
  const labelGroups: Record<string, WithinReceiptWordDecision[]> = {};
  for (const d of format_validation.decisions) {
    const label = d.current_label || "UNKNOWN";
    if (!labelGroups[label]) labelGroups[label] = [];
    labelGroups[label].push(d);
  }

  return (
    <div className={styles.card}>
      <h4 className={styles.cardTitle}>Format Patterns</h4>
      <div className={styles.cardBody}>
        {Object.entries(labelGroups).map(([label, decisions]) => (
          <div key={label} className={styles.patternItem}>
            <span className={styles.patternLabel}>{label.replace(/_/g, ' ')}</span>
            <span className={styles.patternCount}>{decisions.length} word{decisions.length !== 1 ? 's' : ''}</span>
          </div>
        ))}
        {format_validation.decisions.length === 0 && (
          <span className={`${styles.placeValue} ${styles.missing}`}>No format decisions</span>
        )}
        <DecisionTally summary={format_validation.summary} />
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Pass Indicator (two connected dots)
// ---------------------------------------------------------------------------

const PassIndicator: React.FC<{
  passState: PassState;
  activePass: PassName | null;
}> = ({ passState, activePass }) => {
  const passes: PassName[] = ["place", "format"];

  return (
    <div className={styles.passIndicator}>
      {passes.map((pass, i) => {
        const isActive = activePass === pass;
        const isComplete = passState[pass] >= 100;

        return (
          <React.Fragment key={pass}>
            {i > 0 && (
              <div className={`${styles.passConnector} ${passState[passes[i - 1]] >= 100 ? styles.active : ''}`} />
            )}
            <div
              className={`${styles.passDot} ${isActive ? styles.active : ''} ${isComplete ? styles.complete : ''}`}
            >
              {i + 1}
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Right Panel - switches cards based on active pass
// ---------------------------------------------------------------------------

const RightPanel: React.FC<{
  receipt: WithinReceiptVerificationReceipt;
  passState: PassState;
  activePass: PassName | null;
  isTransitioning: boolean;
}> = ({ receipt, passState, activePass, isTransitioning }) => {
  // Show format card during the format pass AND during the hold phase (activePass=null)
  // so the card doesn't flicker back to place after format completes.
  const showFormat = activePass === 'format' || (activePass === null && passState.format >= 100);
  return (
    <div className={styles.rightPanel} style={{ opacity: isTransitioning ? 0 : 1, transition: 'opacity 0.3s ease' }}>
      <PassIndicator passState={passState} activePass={activePass} />
      <div className={styles.cardContainer}>
        {!showFormat && (
          <PlaceCard receipt={receipt} />
        )}
        {showFormat && (
          <div className={styles.cardFadeIn}>
            <FormatPatternsCard receipt={receipt} />
          </div>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

const WithinReceiptVerification: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  const [receipts, setReceipts] = useState<WithinReceiptVerificationReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [phase, setPhase] = useState<Phase>("idle");
  const [passState, setPassState] = useState<PassState>({
    place: 0,
    format: 0,
  });
  const [revealedDecisions, setRevealedDecisions] = useState<RevealedDecision[]>([]);
  const formatSupport = useImageFormatSupport();
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [activePass, setActivePass] = useState<PassName | null>(null);

  usePreloadReceiptImages(receipts, formatSupport);

  const { flyingItem: flyingReceipt, showFlying: showFlyingReceipt } = useFlyingReceipt(
    isTransitioning,
    receipts,
    currentIndex,
  );

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;
  const currentIndexRef = useRef(0);

  // Fetch data
  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await api.fetchWithinReceiptVerification();
        if (response && response.receipts) {
          setReceipts(response.receipts);
        }
      } catch (err) {
        console.error("Failed to fetch within-receipt verification data:", err);
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const currentReceipt = receipts[currentIndex];

  // Build word lookup map once per receipt (O(W) once, then O(1) per lookup)
  const wordLookup = useMemo(() => {
    if (!currentReceipt) return new Map<string, { bbox: { x: number; y: number; width: number; height: number } }>();
    const map = new Map<string, { bbox: { x: number; y: number; width: number; height: number } }>();
    for (const w of currentReceipt.words) {
      map.set(`${w.line_id}_${w.word_id}`, w);
    }
    return map;
  }, [currentReceipt]);

  // Calculate revealed decisions based on pass progress
  useEffect(() => {
    if (!currentReceipt) return;

    const { place_validation, format_validation } = currentReceipt;
    const decisions: RevealedDecision[] = [];

    const getWordIfScanned = (lineId: number, wordId: number, progress: number) => {
      const word = wordLookup.get(`${lineId}_${wordId}`);
      if (!word) return null;
      const wordTopY = 1 - word.bbox.y - word.bbox.height;
      return wordTopY <= (progress / 100) ? word : null;
    };

    // Place decisions
    for (const d of place_validation.decisions) {
      if (!d.decision) continue;
      const word = getWordIfScanned(d.line_id, d.word_id, passState.place);
      if (word) {
        decisions.push({
          key: `place_${d.line_id}_${d.word_id}`,
          pass: 'place',
          currentLabel: d.current_label || 'O',
          wordText: d.word_text,
          lineId: d.line_id,
          wordId: d.word_id,
          bbox: word.bbox,
        });
      }
    }

    // Format decisions
    for (const d of format_validation.decisions) {
      if (!d.decision) continue;
      const word = getWordIfScanned(d.line_id, d.word_id, passState.format);
      if (word) {
        decisions.push({
          key: `format_${d.line_id}_${d.word_id}`,
          pass: 'format',
          currentLabel: d.current_label || 'O',
          wordText: d.word_text,
          lineId: d.line_id,
          wordId: d.word_id,
          bbox: word.bbox,
        });
      }
    }

    setRevealedDecisions(decisions);
  }, [currentReceipt, passState, wordLookup]);

  // Animation loop - sequential passes: Place → Format
  const hasReceipts = receipts.length > 0;
  useEffect(() => {
    if (!inView || !hasReceipts) {
      return;
    }

    if (isAnimatingRef.current) {
      return;
    }
    isAnimatingRef.current = true;

    const placeEnd = PLACE_DURATION;
    const formatStart = placeEnd;
    const formatEnd = formatStart + FORMAT_DURATION;
    const allScannersEnd = formatEnd;
    const holdEnd = allScannersEnd + HOLD_DURATION;
    const totalCycle = holdEnd + TRANSITION_DURATION;

    let receiptIdx = currentIndexRef.current;
    let startTime = performance.now();

    setPhase("scanning");
    setIsTransitioning(false);
    setActivePass("place");
    setPassState({ place: 0, format: 0 });

    let isInTransition = false;

    const animate = (time: number) => {
      const currentReceipts = receiptsRef.current;
      if (currentReceipts.length === 0) {
        isAnimatingRef.current = false;
        return;
      }

      const elapsed = time - startTime;

      if (elapsed < allScannersEnd) {
        const placeProgress = Math.min((elapsed / PLACE_DURATION) * 100, 100);

        let formatProgress = 0;
        if (elapsed >= formatStart) {
          formatProgress = Math.min(((elapsed - formatStart) / FORMAT_DURATION) * 100, 100);
        }

        let currentActivePass: PassName = "place";
        if (elapsed >= formatStart) {
          currentActivePass = "format";
        }

        setPhase("scanning");
        setIsTransitioning(false);
        setActivePass(currentActivePass);
        setPassState({
          place: placeProgress,
          format: formatProgress,
        });
      } else if (elapsed < holdEnd) {
        setPhase("complete");
        setIsTransitioning(false);
        setActivePass(null);
        setPassState({ place: 100, format: 100 });
      } else if (elapsed < totalCycle) {
        if (!isInTransition) {
          isInTransition = true;
          setIsTransitioning(true);
        }
      } else {
        receiptIdx = (receiptIdx + 1) % currentReceipts.length;
        isInTransition = false;
        currentIndexRef.current = receiptIdx;
        setCurrentIndex(receiptIdx);

        setPhase("scanning");
        setIsTransitioning(false);
        setActivePass("place");
        setPassState({ place: 0, format: 0 });
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
  }, [inView, hasReceipts]);

  const flyingElement = useMemo(() => {
    if (!showFlyingReceipt || !flyingReceipt || !formatSupport) return null;
    const imageUrl = getBestImageUrl(flyingReceipt, formatSupport);
    if (!imageUrl) return null;
    const aspect = flyingReceipt.width / flyingReceipt.height;
    let dh = Math.min(500, flyingReceipt.height);
    let dw = dh * aspect;
    if (dw > 350) { dw = 350; dh = dw / aspect; }
    return (
      <FlyingReceipt
        key={`flying-${flyingReceipt.image_id}_${flyingReceipt.receipt_id}`}
        imageUrl={imageUrl}
        displayWidth={dw}
        displayHeight={dh}
        receiptId={`${flyingReceipt.image_id}_${flyingReceipt.receipt_id}`}
        onImageError={(e) => {
          const fallback = getJpegFallbackUrl(flyingReceipt);
          if ((e.target as HTMLImageElement).src !== fallback) {
            (e.target as HTMLImageElement).src = fallback;
          }
        }}
      />
    );
  }, [showFlyingReceipt, flyingReceipt, formatSupport]);

  if (loading) {
    return (
      <div ref={ref} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="within"
        />
      </div>
    );
  }

  if (error) {
    return (
      <div ref={ref} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="within"
          message={`Error: ${error}`}
          isError
        />
      </div>
    );
  }

  if (receipts.length === 0) {
    return (
      <div ref={ref} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="within"
          message="No within-receipt verification data available"
        />
      </div>
    );
  }

  const nextIndex = (currentIndex + 1) % receipts.length;
  const nextReceipt = receipts[nextIndex];

  return (
    <div ref={ref} className={styles.container}>
      <ReceiptFlowShell
        layoutVars={LAYOUT_VARS}
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
            passState={passState}
            phase={phase}
            revealedDecisions={revealedDecisions}
            formatSupport={formatSupport}
          />
        }
        flying={flyingElement}
        next={
          isTransitioning && nextReceipt ? (
            <ReceiptViewer
              receipt={nextReceipt}
              passState={{ place: 0, format: 0 }}
              phase="idle"
              revealedDecisions={[]}
              formatSupport={formatSupport}
            />
          ) : null
        }
        legend={
          <RightPanel
          receipt={currentReceipt}
          passState={passState}
          activePass={activePass}
          isTransitioning={isTransitioning}
          />
        }
      />
    </div>
  );
};

export default WithinReceiptVerification;
