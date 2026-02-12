import { animated, to, useSpring } from "@react-spring/web";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  WithinReceiptVerificationReceipt,
  WithinReceiptWordDecision,
} from "../../../../types/api";
import { detectImageFormatSupport, getBestImageUrl } from "../../../../utils/imageFormat";
import styles from "./WithinReceiptVerification.module.css";

// ---------------------------------------------------------------------------
// Types & Constants
// ---------------------------------------------------------------------------

type PassName = "place" | "format" | "financial";
type Phase = "idle" | "scanning" | "complete";

interface PassState {
  place: number;    // 0-100
  format: number;   // 0-100
  financial: number; // 0-100
}

interface RevealedDecision {
  key: string;
  pass: PassName;
  decision: "VALID" | "INVALID" | "NEEDS_REVIEW";
  wordText: string;
  lineId: number;
  wordId: number;
  bbox: { x: number; y: number; width: number; height: number };
}

// Pass colors
const PASS_COLORS: Record<PassName, string> = {
  place: "var(--color-blue)",
  format: "var(--color-teal, #2dd4bf)",
  financial: "var(--color-purple)",
};

// Decision colors
const DECISION_COLORS: Record<string, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  NEEDS_REVIEW: "var(--color-yellow)",
};

// Pass labels for UI
const PASS_LABELS: Record<PassName, string> = {
  place: "Place Validation",
  format: "Format Validation",
  financial: "Financial Math",
};

// Animation timing
const PLACE_DURATION = 2000;
const FORMAT_DURATION = 1500;
const FINANCIAL_DURATION = 2500;
const HOLD_DURATION = 1000;
const TRANSITION_DURATION = 600;

// Layout constants
const QUEUE_WIDTH = 120;
const QUEUE_ITEM_WIDTH = 100;
const QUEUE_ITEM_LEFT_INSET = 10;
const CENTER_COLUMN_WIDTH = 350;
const CENTER_COLUMN_HEIGHT = 500;
const QUEUE_HEIGHT = 400;
const COLUMN_GAP = 24;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const getQueuePosition = (receiptId: string) => {
  const hash = receiptId.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const random1 = Math.sin(hash * 9301 + 49297) % 1;
  const random2 = Math.sin(hash * 7919 + 12345) % 1;
  const rotation = (Math.abs(random1) * 24 - 12);
  const leftOffset = (Math.abs(random2) * 10 - 5);
  return { rotation, leftOffset };
};

// ---------------------------------------------------------------------------
// Receipt Queue
// ---------------------------------------------------------------------------

interface ReceiptQueueProps {
  receipts: WithinReceiptVerificationReceipt[];
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
    const result: WithinReceiptVerificationReceipt[] = [];
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
                width={width}
                height={height}
                style={{ width: "100%", height: "auto", display: "block" }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Flying Receipt
// ---------------------------------------------------------------------------

interface FlyingReceiptProps {
  receipt: WithinReceiptVerificationReceipt | null;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  isFlying: boolean;
}

const FlyingReceipt: React.FC<FlyingReceiptProps> = ({ receipt, formatSupport, isFlying }) => {
  const width = Math.max(receipt?.width ?? 100, 1);
  const height = Math.max(receipt?.height ?? 150, 1);
  const receiptId = receipt ? `${receipt.image_id}_${receipt.receipt_id}` : '';
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

  const distanceToQueueItemCenter = (CENTER_COLUMN_WIDTH / 2) + COLUMN_GAP + (QUEUE_WIDTH - (QUEUE_ITEM_LEFT_INSET + leftOffset + QUEUE_ITEM_WIDTH / 2));
  const startX = -distanceToQueueItemCenter;
  const queueItemHeight = (height / width) * QUEUE_ITEM_WIDTH;
  const queueItemCenterFromTop = ((CENTER_COLUMN_HEIGHT - QUEUE_HEIGHT) / 2) + (queueItemHeight / 2);
  const startY = queueItemCenterFromTop - (CENTER_COLUMN_HEIGHT / 2);
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
        style={{ width: displayWidth, height: displayHeight }}
      />
    </animated.div>
  );
};

// ---------------------------------------------------------------------------
// Receipt Viewer (SVG overlay with scan lines + decision indicators)
// ---------------------------------------------------------------------------

interface ReceiptViewerProps {
  receipt: WithinReceiptVerificationReceipt;
  passState: PassState;
  phase: Phase;
  revealedDecisions: RevealedDecision[];
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
}

const ReceiptViewer: React.FC<ReceiptViewerProps> = ({
  receipt,
  passState,
  phase,
  revealedDecisions,
  formatSupport,
}) => {
  const { width, height } = receipt;
  const filterId = `scanLineGlow_wr_${receipt.image_id}_${receipt.receipt_id}`;

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport);
  }, [receipt, formatSupport]);

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  const placeY = (passState.place / 100) * height;
  const formatY = (passState.format / 100) * height;
  const financialY = (passState.financial / 100) * height;

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
              <filter id={filterId} x="-50%" y="-50%" width="200%" height="200%">
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
              const x = d.bbox.x * width;
              const y = (1 - d.bbox.y - d.bbox.height) * height;
              const w = d.bbox.width * width;
              const h = d.bbox.height * height;
              const centerX = x + w / 2;
              const centerY = y + h / 2;
              const circleRadius = Math.min(w, h) * 0.4;
              const iconSize = circleRadius * 0.6;

              return (
                <g key={`indicator_${d.key}`} className={styles.decisionIndicator}>
                  <rect
                    x={x} y={y} width={w} height={h}
                    fill={color} fillOpacity={0.3}
                    stroke={color} strokeWidth={2}
                  />
                  <circle cx={centerX} cy={centerY} r={circleRadius} fill={color} />
                  {d.decision === 'VALID' && (
                    <path
                      d={`M ${centerX - iconSize * 0.8} ${centerY}
                          L ${centerX - iconSize * 0.2} ${centerY + iconSize * 0.6}
                          L ${centerX + iconSize * 0.8} ${centerY - iconSize * 0.5}`}
                      fill="none" stroke="white"
                      strokeWidth={iconSize * 0.35}
                      strokeLinecap="round" strokeLinejoin="round"
                    />
                  )}
                  {d.decision === 'INVALID' && (
                    <g>
                      <line
                        x1={centerX - iconSize * 0.5} y1={centerY - iconSize * 0.5}
                        x2={centerX + iconSize * 0.5} y2={centerY + iconSize * 0.5}
                        stroke="white" strokeWidth={iconSize * 0.35} strokeLinecap="round"
                      />
                      <line
                        x1={centerX + iconSize * 0.5} y1={centerY - iconSize * 0.5}
                        x2={centerX - iconSize * 0.5} y2={centerY + iconSize * 0.5}
                        stroke="white" strokeWidth={iconSize * 0.35} strokeLinecap="round"
                      />
                    </g>
                  )}
                  {d.decision === 'NEEDS_REVIEW' && (
                    <g>
                      <circle cx={centerX} cy={centerY - iconSize * 0.35} r={iconSize * 0.3} fill="white" />
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

            {/* Scan lines */}
            {passState.place > 0 && passState.place < 100 && (
              <rect
                x="0" y={placeY} width={width}
                height={Math.max(height * 0.006, 3)}
                fill={PASS_COLORS.place}
                filter={`url(#${filterId})`}
              />
            )}
            {passState.format > 0 && passState.format < 100 && (
              <rect
                x="0" y={formatY} width={width}
                height={Math.max(height * 0.006, 3)}
                fill={PASS_COLORS.format}
                filter={`url(#${filterId})`}
              />
            )}
            {passState.financial > 0 && passState.financial < 100 && (
              <rect
                x="0" y={financialY} width={width}
                height={Math.max(height * 0.006, 3)}
                fill={PASS_COLORS.financial}
                filter={`url(#${filterId})`}
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
          <span className={styles.tallyDot} style={{ background: DECISION_COLORS.VALID }} />
          <span className={styles.tallyCount}>{summary.valid}</span>
        </div>
      )}
      {summary.invalid > 0 && (
        <div className={styles.tallyItem}>
          <span className={styles.tallyDot} style={{ background: DECISION_COLORS.INVALID }} />
          <span className={styles.tallyCount}>{summary.invalid}</span>
        </div>
      )}
      {summary.needs_review > 0 && (
        <div className={styles.tallyItem}>
          <span className={styles.tallyDot} style={{ background: DECISION_COLORS.NEEDS_REVIEW }} />
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
                      backgroundColor: place.confidence > 0.7 ? DECISION_COLORS.VALID
                        : place.confidence > 0.4 ? DECISION_COLORS.NEEDS_REVIEW
                        : DECISION_COLORS.INVALID,
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

const EquationsCard: React.FC<{ receipt: WithinReceiptVerificationReceipt }> = ({ receipt }) => {
  const { financial_math } = receipt;

  return (
    <div className={styles.card}>
      <h4 className={styles.cardTitle}>Equations</h4>
      <div className={styles.cardBody}>
        {financial_math.equations.slice(0, 5).map((eq, i) => (
          <div key={i} className={styles.equationItem}>
            <span className={styles.equationDesc}>{eq.description}</span>
            <div className={styles.equationValues}>
              {eq.expected_value != null && (
                <span className={styles.equationExpected}>exp: {eq.expected_value}</span>
              )}
              {eq.actual_value != null && (
                <span className={styles.equationActual}>act: {eq.actual_value}</span>
              )}
              {eq.difference != null && (
                <span className={`${styles.equationDiff} ${eq.difference === 0 ? styles.zero : ''}`}>
                  diff: {eq.difference}
                </span>
              )}
            </div>
          </div>
        ))}
        {financial_math.equations.length === 0 && (
          <span className={`${styles.placeValue} ${styles.missing}`}>No equations</span>
        )}
        {financial_math.equations.length > 5 && (
          <span className={`${styles.placeValue} ${styles.missing}`}>+{financial_math.equations.length - 5} more</span>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Pass Indicator (three connected dots)
// ---------------------------------------------------------------------------

const PassIndicator: React.FC<{
  passState: PassState;
  activePass: PassName | null;
}> = ({ passState, activePass }) => {
  const passes: PassName[] = ["place", "format", "financial"];

  return (
    <div className={styles.passIndicator}>
      {passes.map((pass, i) => {
        const progress = passState[pass];
        const isActive = activePass === pass;
        const isComplete = progress >= 100;

        return (
          <React.Fragment key={pass}>
            {i > 0 && (
              <div className={`${styles.passConnector} ${passState[passes[i - 1]] >= 100 ? styles.active : ''}`} />
            )}
            <div className={styles.passStep}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px' }}>
                <div
                  className={`${styles.passDot} ${isActive ? styles.active : ''} ${isComplete ? styles.complete : ''}`}
                  style={{ backgroundColor: PASS_COLORS[pass] }}
                >
                  {i + 1}
                </div>
                <div className={styles.passProgress}>
                  <div
                    className={styles.passProgressFill}
                    style={{ width: `${progress}%`, backgroundColor: PASS_COLORS[pass] }}
                  />
                </div>
                {isComplete && (
                  <span className={styles.durationBadge}>
                    {pass === 'place' && receipt_duration(passState, 'place')}
                    {pass === 'format' && receipt_duration(passState, 'format')}
                    {pass === 'financial' && receipt_duration(passState, 'financial')}
                  </span>
                )}
              </div>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};

// Placeholder for duration display in pass indicator
function receipt_duration(_passState: PassState, _pass: PassName): string {
  return '';
}

// ---------------------------------------------------------------------------
// Right Panel - switches cards based on active pass
// ---------------------------------------------------------------------------

const RightPanel: React.FC<{
  receipt: WithinReceiptVerificationReceipt;
  passState: PassState;
  activePass: PassName | null;
}> = ({ receipt, passState, activePass }) => {
  return (
    <div className={styles.rightPanel}>
      <PassIndicator passState={passState} activePass={activePass} />
      <div className={styles.cardContainer}>
        {(!activePass || activePass === 'place') && (
          <div className={styles.card}>
            <PlaceCard receipt={receipt} />
          </div>
        )}
        {activePass === 'format' && (
          <div className={`${styles.card} ${styles.cardFadeIn}`}>
            <FormatPatternsCard receipt={receipt} />
          </div>
        )}
        {activePass === 'financial' && (
          <div className={`${styles.card} ${styles.cardFadeIn}`}>
            <EquationsCard receipt={receipt} />
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
    financial: 0,
  });
  const [revealedDecisions, setRevealedDecisions] = useState<RevealedDecision[]>([]);
  const [formatSupport, setFormatSupport] = useState<{
    supportsWebP: boolean;
    supportsAVIF: boolean;
  } | null>(null);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [showFlyingReceipt, setShowFlyingReceipt] = useState(false);
  const [flyingReceipt, setFlyingReceipt] = useState<WithinReceiptVerificationReceipt | null>(null);
  const [activePass, setActivePass] = useState<PassName | null>(null);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Flying receipt visibility
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

  // Calculate revealed decisions based on pass progress
  useEffect(() => {
    if (!currentReceipt) return;

    const { words, place_validation, format_validation, financial_math } = currentReceipt;
    const decisions: RevealedDecision[] = [];

    const isWordScanned = (lineId: number, wordId: number, progress: number) => {
      const word = words.find(w => w.line_id === lineId && w.word_id === wordId);
      if (!word) return false;
      const wordTopY = 1 - word.bbox.y - word.bbox.height;
      return wordTopY <= (progress / 100);
    };

    // Place decisions
    place_validation.decisions.forEach((d) => {
      if (d.decision && isWordScanned(d.line_id, d.word_id, passState.place)) {
        const word = words.find(w => w.line_id === d.line_id && w.word_id === d.word_id);
        if (word) {
          decisions.push({
            key: `place_${d.line_id}_${d.word_id}`,
            pass: 'place',
            decision: d.decision,
            wordText: d.word_text,
            lineId: d.line_id,
            wordId: d.word_id,
            bbox: word.bbox,
          });
        }
      }
    });

    // Format decisions
    format_validation.decisions.forEach((d) => {
      if (d.decision && isWordScanned(d.line_id, d.word_id, passState.format)) {
        const word = words.find(w => w.line_id === d.line_id && w.word_id === d.word_id);
        if (word) {
          decisions.push({
            key: `format_${d.line_id}_${d.word_id}`,
            pass: 'format',
            decision: d.decision,
            wordText: d.word_text,
            lineId: d.line_id,
            wordId: d.word_id,
            bbox: word.bbox,
          });
        }
      }
    });

    // Financial decisions (from equation involved_words)
    financial_math.equations.forEach((eq, eqIdx) => {
      eq.involved_words.forEach((d) => {
        if (d.decision && isWordScanned(d.line_id, d.word_id, passState.financial)) {
          const word = words.find(w => w.line_id === d.line_id && w.word_id === d.word_id);
          if (word) {
            decisions.push({
              key: `financial_${eqIdx}_${d.line_id}_${d.word_id}`,
              pass: 'financial',
              decision: d.decision,
              wordText: d.word_text,
              lineId: d.line_id,
              wordId: d.word_id,
              bbox: word.bbox,
            });
          }
        }
      });
    });

    setRevealedDecisions(decisions);
  }, [currentReceipt, passState]);

  // Animation loop - sequential passes
  useEffect(() => {
    if (!inView || receipts.length === 0) {
      return;
    }

    if (isAnimatingRef.current) {
      return;
    }
    isAnimatingRef.current = true;

    // Sequential timing: Place → Format → Financial
    const placeEnd = PLACE_DURATION;
    const formatStart = placeEnd;
    const formatEnd = formatStart + FORMAT_DURATION;
    const financialStart = formatEnd;
    const financialEnd = financialStart + FINANCIAL_DURATION;
    const allScannersEnd = financialEnd;
    const holdEnd = allScannersEnd + HOLD_DURATION;
    const totalCycle = holdEnd + TRANSITION_DURATION;

    let receiptIdx = currentIndex;
    let startTime = performance.now();

    setPhase("scanning");
    setIsTransitioning(false);
    setActivePass("place");
    setPassState({ place: 0, format: 0, financial: 0 });

    let isInTransition = false;

    const animate = (time: number) => {
      const currentReceipts = receiptsRef.current;
      if (currentReceipts.length === 0) {
        isAnimatingRef.current = false;
        return;
      }

      const elapsed = time - startTime;

      if (elapsed < allScannersEnd) {
        // Pass 1: Place
        const placeProgress = Math.min((elapsed / PLACE_DURATION) * 100, 100);

        // Pass 2: Format (after Place completes)
        let formatProgress = 0;
        if (elapsed >= formatStart) {
          formatProgress = Math.min(((elapsed - formatStart) / FORMAT_DURATION) * 100, 100);
        }

        // Pass 3: Financial (after Format completes)
        let financialProgress = 0;
        if (elapsed >= financialStart) {
          financialProgress = Math.min(((elapsed - financialStart) / FINANCIAL_DURATION) * 100, 100);
        }

        // Determine active pass
        let currentActivePass: PassName = "place";
        if (elapsed >= financialStart) {
          currentActivePass = "financial";
        } else if (elapsed >= formatStart) {
          currentActivePass = "format";
        }

        setPhase("scanning");
        setIsTransitioning(false);
        setActivePass(currentActivePass);
        setPassState({
          place: placeProgress,
          format: formatProgress,
          financial: financialProgress,
        });
      } else if (elapsed < holdEnd) {
        // Hold phase
        setPhase("complete");
        setIsTransitioning(false);
        setActivePass(null);
        setPassState({ place: 100, format: 100, financial: 100 });
      } else if (elapsed < totalCycle) {
        // Transition phase
        if (!isInTransition) {
          isInTransition = true;
          setIsTransitioning(true);
        }
      } else {
        // Next receipt
        receiptIdx = (receiptIdx + 1) % currentReceipts.length;
        isInTransition = false;
        setCurrentIndex(receiptIdx);

        setPhase("scanning");
        setIsTransitioning(false);
        setActivePass("place");
        setPassState({ place: 0, format: 0, financial: 0 });
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
  }, [inView, receipts.length > 0, currentIndex]);

  if (loading) {
    return (
      <div ref={ref} className={styles.loading}>
        Loading within-receipt verification data...
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
        No within-receipt verification data available
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
              passState={passState}
              phase={phase}
              revealedDecisions={revealedDecisions}
              formatSupport={formatSupport}
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
            <div className={`${styles.receiptContainer} ${styles.nextReceipt} ${styles.fadeIn}`}>
              <ReceiptViewer
                receipt={nextReceipt}
                passState={{ place: 0, format: 0, financial: 0 }}
                phase="idle"
                revealedDecisions={[]}
                formatSupport={formatSupport}
              />
            </div>
          )}
        </div>

        <RightPanel
          receipt={currentReceipt}
          passState={passState}
          activePass={activePass}
        />
      </div>
    </div>
  );
};

export default WithinReceiptVerification;
