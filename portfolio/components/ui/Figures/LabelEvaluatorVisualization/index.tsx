import React, { useEffect, useState, useMemo, useRef } from "react";
import { useInView } from "react-intersection-observer";
import { animated, useTransition } from "@react-spring/web";
import { api } from "../../../../services/api";
import {
  LabelEvaluatorReceipt,
} from "../../../../types/api";
import { detectImageFormatSupport, getBestImageUrl } from "../../../../utils/imageFormat";
import styles from "./LabelEvaluatorVisualization.module.css";

// Animation state - each scanner has independent progress
interface ScannerState {
  lineItem: number;      // 0-100
  metadata: number;      // 0-100
  geometric: number;     // 0-100
  financial: number;     // 0-100
  currency: number;      // 0-100 (starts after lineItem completes)
  review: number;        // 0-100 (conditional)
}

type Phase = "idle" | "scanning" | "complete";

// Scanner colors - grouped by dependency chain
// Using CSS variables for automatic light/dark mode support
const SCANNER_COLORS = {
  // Chain 1: Line Item → Currency → Financial (purple)
  // Financial validates currency labels (GRAND_TOTAL, SUBTOTAL, TAX, etc.)
  // It needs Currency's corrections but NOT Metadata's
  lineItem: "var(--color-purple)",
  currency: "var(--color-purple)",
  financial: "var(--color-purple)",
  // Independent: Metadata (blue) - no data flows to Financial
  metadata: "var(--color-blue)",
  // Chain 2: Geometric → Review (orange)
  geometric: "var(--color-orange)",
  review: "var(--color-orange)",
};

// Decision colors - using CSS variables
const DECISION_COLORS: Record<string, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  NEEDS_REVIEW: "var(--color-yellow)",
};

// Revealed decision for tracking individual V/I/R decisions as scanner progresses
interface RevealedDecision {
  key: string;                    // "metadata_1_3"
  type: 'currency' | 'metadata' | 'financial' | 'review';
  decision: "VALID" | "INVALID" | "NEEDS_REVIEW";
  wordText: string;
  lineId: number;
  wordId: number;
  bbox: { x: number; y: number; width: number; height: number };
}

// Animation timing (ms)
const TARGET_TOTAL_DURATION = 6000;  // Target ~6 seconds for all phases combined
const MIN_PHASE_DURATION = 800;      // Minimum animation duration for visibility
const HOLD_DURATION = 2000;
const TRANSITION_DURATION = 500;

interface ScannerBarProps {
  name: string;
  color: string;
  progress: number;
  isActive: boolean;
  isComplete: boolean;
  isLLM: boolean;
  isWaiting?: boolean;
  waitingFor?: string;
  durationMs?: number;
  decisions?: { VALID: number; INVALID: number; NEEDS_REVIEW: number };
}

const ScannerBar: React.FC<ScannerBarProps> = ({
  name,
  color,
  progress,
  isActive,
  isComplete,
  isLLM,
  isWaiting,
  waitingFor,
  durationMs,
  decisions,
}) => {
  const totalDecisions = decisions
    ? decisions.VALID + decisions.INVALID + decisions.NEEDS_REVIEW
    : 0;

  return (
    <div className={`${styles.scannerBar} ${isActive ? styles.active : ""} ${isComplete ? styles.complete : ""} ${isWaiting ? styles.waiting : ""}`}>
      <div className={styles.scannerHeader}>
        <div className={styles.scannerNameWrapper}>
          <span className={styles.scannerName}>{name}</span>
          <span className={styles.scannerType}>{isLLM ? "LLM" : "Deterministic"}</span>
          {isWaiting && waitingFor && (
            <span className={styles.waitingBadge}>waiting for {waitingFor}</span>
          )}
        </div>
        <div className={styles.scannerMeta}>
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
              {decisions.INVALID > 0 && (
                <span
                  className={styles.badge}
                  style={{ backgroundColor: DECISION_COLORS.INVALID }}
                >
                  {decisions.INVALID}
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

// Decision tally component - shows ✓/✗/👤 icons as scanner reveals decisions
interface DecisionTallyProps {
  scannerType: 'currency' | 'metadata' | 'financial' | 'review';
  revealedDecisions: RevealedDecision[];
  totalDecisions: number;
}

const DecisionTally: React.FC<DecisionTallyProps> = ({
  scannerType,
  revealedDecisions,
  totalDecisions,
}) => {
  const filtered = revealedDecisions.filter(d => d.type === scannerType);

  // Staggered animation using react-spring
  const transitions = useTransition(filtered, {
    keys: (item) => item.key,
    from: { opacity: 0, transform: 'scale(0.5)' },
    enter: { opacity: 1, transform: 'scale(1)' },
    config: { tension: 300, friction: 20 },
  });

  const getIcon = (decision: string) => {
    switch (decision) {
      case 'VALID': return '✓';
      case 'INVALID': return '✗';
      case 'NEEDS_REVIEW': return '👤';
      default: return '?';
    }
  };

  if (totalDecisions === 0) return null;

  return (
    <div className={styles.tallyRow}>
      <div className={styles.tallyIcons}>
        {transitions((style, item) => (
          <animated.span
            key={item.key}
            className={styles.tallyIcon}
            style={{ ...style, color: DECISION_COLORS[item.decision] }}
            title={`${item.wordText}: ${item.decision}`}
          >
            {getIcon(item.decision)}
          </animated.span>
        ))}
      </div>
      <span className={styles.tallyCounter}>
        {filtered.length}/{totalDecisions}
      </span>
    </div>
  );
};

interface ReceiptViewerProps {
  receipt: LabelEvaluatorReceipt;
  scannerState: ScannerState;
  phase: Phase;
  revealedDecisions: RevealedDecision[];
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
}

const ReceiptViewer: React.FC<ReceiptViewerProps> = ({
  receipt,
  scannerState,
  phase,
  revealedDecisions,
  formatSupport,
}) => {
  const { words, width, height } = receipt;

  // Get the best image URL based on format support
  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport, 'medium');
  }, [receipt, formatSupport]);

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  // Calculate scan line Y positions based on each scanner's progress
  const lineItemY = (scannerState.lineItem / 100) * height;
  const metadataY = (scannerState.metadata / 100) * height;
  const geometricY = (scannerState.geometric / 100) * height;
  const financialY = (scannerState.financial / 100) * height;
  const currencyY = (scannerState.currency / 100) * height;
  const reviewY = (scannerState.review / 100) * height;

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
          {/* SVG overlay for bounding boxes and scan lines */}
          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
          >
            {/* Gradient definitions for scan lines */}
            <defs>
              {/* Glow filter */}
              <filter id="scanLineGlow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>

            {/* Decision bounding boxes - colored by V/I/R decision */}
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
                  {/* Icon SVG paths */}
                  {d.decision === 'VALID' && (
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
                  {d.decision === 'INVALID' && (
                    <g>
                      <line
                        x1={centerX - iconSize * 0.5}
                        y1={centerY - iconSize * 0.5}
                        x2={centerX + iconSize * 0.5}
                        y2={centerY + iconSize * 0.5}
                        stroke="white"
                        strokeWidth={iconSize * 0.35}
                        strokeLinecap="round"
                      />
                      <line
                        x1={centerX + iconSize * 0.5}
                        y1={centerY - iconSize * 0.5}
                        x2={centerX - iconSize * 0.5}
                        y2={centerY + iconSize * 0.5}
                        stroke="white"
                        strokeWidth={iconSize * 0.35}
                        strokeLinecap="round"
                      />
                    </g>
                  )}
                  {d.decision === 'NEEDS_REVIEW' && (
                    <g>
                      {/* Head */}
                      <circle
                        cx={centerX}
                        cy={centerY - iconSize * 0.35}
                        r={iconSize * 0.3}
                        fill="white"
                      />
                      {/* Body */}
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

            {/* Each scanner has its own scan line that moves independently */}

            {/* Line Item Structure (deep orange) - active while in progress */}
            {scannerState.lineItem > 0 && scannerState.lineItem < 100 && (
              <rect
                x="0"
                y={lineItemY}
                width={width}
                height={Math.max(height * 0.004, 2)}
                fill={SCANNER_COLORS.lineItem}
                filter="url(#scanLineGlow)"
              />
            )}

            {/* Metadata (blue) - active while in progress */}
            {scannerState.metadata > 0 && scannerState.metadata < 100 && (
              <rect
                x="0"
                y={metadataY}
                width={width}
                height={Math.max(height * 0.004, 2)}
                fill={SCANNER_COLORS.metadata}
                filter="url(#scanLineGlow)"
              />
            )}

            {/* Geometric (purple) - active while in progress */}
            {scannerState.geometric > 0 && scannerState.geometric < 100 && (
              <rect
                x="0"
                y={geometricY}
                width={width}
                height={Math.max(height * 0.004, 2)}
                fill={SCANNER_COLORS.geometric}
                filter="url(#scanLineGlow)"
              />
            )}

            {/* Financial (orange) - active while in progress */}
            {scannerState.financial > 0 && scannerState.financial < 100 && (
              <rect
                x="0"
                y={financialY}
                width={width}
                height={Math.max(height * 0.004, 2)}
                fill={SCANNER_COLORS.financial}
                filter="url(#scanLineGlow)"
              />
            )}

            {/* Currency (green) - starts after Line Item completes */}
            {scannerState.currency > 0 && scannerState.currency < 100 && (
              <rect
                x="0"
                y={currencyY}
                width={width}
                height={Math.max(height * 0.004, 2)}
                fill={SCANNER_COLORS.currency}
                filter="url(#scanLineGlow)"
              />
            )}

            {/* Review (pink) - conditional, after all others */}
            {scannerState.review > 0 && scannerState.review < 100 && (
              <rect
                x="0"
                y={reviewY}
                width={width}
                height={Math.max(height * 0.004, 2)}
                fill={SCANNER_COLORS.review}
                filter="url(#scanLineGlow)"
              />
            )}
          </svg>
        </div>
      </div>
      <div className={styles.phaseIndicator}>
        {phase === "scanning" && "Evaluating..."}
        {phase === "complete" && "Evaluation Complete"}
        {phase === "idle" && "Ready"}
      </div>
    </div>
  );
};

interface PipelinePanelProps {
  receipt: LabelEvaluatorReceipt;
  scannerState: ScannerState;
  revealedDecisions: RevealedDecision[];
}

const PipelinePanel: React.FC<PipelinePanelProps> = ({
  receipt,
  scannerState,
  revealedDecisions,
}) => {
  const { currency, metadata, geometric, financial } = receipt;

  // Check if we have geometric issues (triggers review phase)
  const hasGeometricIssues = geometric.issues_found > 0;

  // Currency waits for Line Item to complete
  const currencyIsWaiting = scannerState.lineItem < 100 && scannerState.currency === 0;

  return (
    <div className={styles.pipelinePanel}>
      {/* Start Together: Line Item + Metadata + Geometric + Financial */}
      <div className={styles.pipelineSection}>
        <h4 className={styles.sectionTitle}>
          <span className={styles.phaseNumber}>1</span>
          Start Together
        </h4>
        <div className={styles.parallelGroup}>
          <ScannerBar
            name="Line Item Structure"
            color={SCANNER_COLORS.lineItem}
            progress={scannerState.lineItem}
            isActive={scannerState.lineItem > 0 && scannerState.lineItem < 100}
            isComplete={scannerState.lineItem >= 100}
            isLLM={true}
            durationMs={scannerState.lineItem >= 100 && receipt.line_item_duration_seconds
              ? receipt.line_item_duration_seconds * 1000
              : undefined}
          />
          <div className={styles.scannerWithTally}>
            <ScannerBar
              name="Metadata"
              color={SCANNER_COLORS.metadata}
              progress={scannerState.metadata}
              isActive={scannerState.metadata > 0 && scannerState.metadata < 100}
              isComplete={scannerState.metadata >= 100}
              isLLM={true}
              durationMs={scannerState.metadata >= 100 ? metadata.duration_seconds * 1000 : undefined}
              decisions={metadata.decisions}
            />
            <DecisionTally
              scannerType="metadata"
              revealedDecisions={revealedDecisions}
              totalDecisions={metadata.all_decisions.length}
            />
          </div>
          <ScannerBar
            name="Geometric"
            color={SCANNER_COLORS.geometric}
            progress={scannerState.geometric}
            isActive={scannerState.geometric > 0 && scannerState.geometric < 100}
            isComplete={scannerState.geometric >= 100}
            isLLM={false}
            durationMs={scannerState.geometric >= 100 ? (geometric.duration_seconds || 0.1) * 1000 : undefined}
            decisions={{
              VALID: 0,
              INVALID: geometric.issues_found,
              NEEDS_REVIEW: 0,
            }}
          />
          <div className={styles.scannerWithTally}>
            <ScannerBar
              name="Financial"
              color={SCANNER_COLORS.financial}
              progress={scannerState.financial}
              isActive={scannerState.financial > 0 && scannerState.financial < 100}
              isComplete={scannerState.financial >= 100}
              isLLM={true}
              durationMs={scannerState.financial >= 100 ? financial.duration_seconds * 1000 : undefined}
              decisions={financial.decisions}
            />
            <DecisionTally
              scannerType="financial"
              revealedDecisions={revealedDecisions}
              totalDecisions={financial.all_decisions.length}
            />
          </div>
        </div>
      </div>

      {/* After Line Item: Currency */}
      <div className={styles.pipelineSection}>
        <h4 className={styles.sectionTitle}>
          <span className={styles.phaseNumber}>2</span>
          After Line Item
        </h4>
        <div className={styles.scannerWithTally}>
          <ScannerBar
            name="Currency"
            color={SCANNER_COLORS.currency}
            progress={scannerState.currency}
            isActive={scannerState.currency > 0 && scannerState.currency < 100}
            isComplete={scannerState.currency >= 100}
            isLLM={true}
            isWaiting={currencyIsWaiting}
            waitingFor="Line Item"
            durationMs={scannerState.currency >= 100 ? currency.duration_seconds * 1000 : undefined}
            decisions={currency.decisions}
          />
          <DecisionTally
            scannerType="currency"
            revealedDecisions={revealedDecisions}
            totalDecisions={currency.all_decisions.length}
          />
        </div>
      </div>

      {/* Conditional: Review Flagged */}
      {hasGeometricIssues && (
        <div className={styles.pipelineSection}>
          <h4 className={styles.sectionTitle}>
            <span className={styles.phaseNumber}>3</span>
            Review Flagged
          </h4>
          <ScannerBar
            name="Review Flagged"
            color={SCANNER_COLORS.review}
            progress={scannerState.review}
            isActive={scannerState.review > 0 && scannerState.review < 100}
            isComplete={scannerState.review >= 100}
            isLLM={true}
            durationMs={scannerState.review >= 100 ? 1000 : undefined}
            decisions={{
              VALID: 0,
              INVALID: 0,
              NEEDS_REVIEW: geometric.issues_found,
            }}
          />
        </div>
      )}

      <div className={styles.summarySection}>
        <h4 className={styles.sectionTitle}>Summary</h4>
        <div className={styles.summaryStats}>
          <div className={styles.statItem}>
            <span className={styles.statLabel}>Issues Found</span>
            <span className={styles.statValue}>{receipt.issues_found}</span>
          </div>
          <div className={styles.statItem}>
            <span className={styles.statLabel}>Words Analyzed</span>
            <span className={styles.statValue}>{receipt.words.length}</span>
          </div>
        </div>
      </div>
    </div>
  );
};

// Scanner legend item - shows colored circle or hourglass + name + status + decision tally
interface ScannerLegendItemProps {
  name: string;
  color: string;
  progress: number;
  isWaiting: boolean;
  isComplete: boolean;
  isSkipped?: boolean;
  durationMs?: number;
  decisions?: RevealedDecision[];
  totalDecisions?: number;
}

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

const ScannerLegendItem: React.FC<ScannerLegendItemProps> = ({
  name,
  color,
  progress,
  isWaiting,
  isComplete,
  isSkipped = false,
  durationMs,
  decisions = [],
  totalDecisions = 0,
}) => {
  const isActive = progress > 0 && progress < 100;
  const hasDecisions = totalDecisions > 0;

  return (
    <div className={`${styles.legendItem} ${isActive ? styles.active : ""} ${isComplete ? styles.complete : ""} ${isSkipped ? styles.skipped : ""}`}>
      <div className={styles.legendIcon}>
        {isSkipped ? (
          // Skipped indicator - dashed circle with dash through
          <svg width="16" height="16" viewBox="0 0 16 16" className={styles.legendDot}>
            <circle
              cx="8"
              cy="8"
              r="6"
              fill="none"
              stroke={color}
              strokeWidth="1.5"
              strokeDasharray="3 2"
              opacity={0.4}
            />
            <line
              x1="4"
              y1="8"
              x2="12"
              y2="8"
              stroke={color}
              strokeWidth="1.5"
              opacity={0.4}
            />
          </svg>
        ) : isWaiting ? (
          // Hourglass SVG icon
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className={styles.hourglassIcon}>
            <path
              d="M4 2h8v3l-2.5 3L12 11v3H4v-3l2.5-3L4 5V2z"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              fill="none"
            />
            <path
              d="M6 3h4M6 13h4"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        ) : (
          // Progress circle that fills like a clock (pie slice from 12 o'clock)
          <svg width="16" height="16" viewBox="0 0 16 16" className={styles.legendDot}>
            {/* Background circle (unfilled outline) */}
            <circle
              cx="8"
              cy="8"
              r="6"
              fill="none"
              stroke={color}
              strokeWidth="1.5"
              opacity={0.3}
            />
            {/* Pie slice fill - grows clockwise from 12 o'clock */}
            {progress > 0 && (
              <path
                d={getPieSlicePath(progress, 8, 8, 6)}
                fill={color}
                opacity={isComplete ? 1 : 0.8}
              />
            )}
          </svg>
        )}
      </div>
      <span className={styles.legendName}>{name}</span>
      <div className={styles.legendStatus}>
        {isSkipped ? (
          <span className={styles.legendSkipped}>skipped</span>
        ) : isComplete && durationMs !== undefined ? (
          <span className={styles.legendDuration}>
            {durationMs < 1000 ? `${durationMs.toFixed(0)}ms` : `${(durationMs / 1000).toFixed(1)}s`}
          </span>
        ) : isWaiting ? (
          <span className={styles.legendWaiting}>waiting</span>
        ) : null}
      </div>
      {/* Decision tally row - colored circles with white icons (matches bounding boxes) */}
      {hasDecisions && decisions.length > 0 && (
        <div className={styles.legendTally}>
          {decisions.map((d) => {
            const bgColor = DECISION_COLORS[d.decision];
            return (
              <span
                key={d.key}
                className={styles.tallyIcon}
                title={`${d.wordText}: ${d.decision}`}
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  {/* Background circle */}
                  <circle cx="7" cy="7" r="6" fill={bgColor} />
                  {/* White icon inside */}
                  {d.decision === 'VALID' && (
                    <path
                      d="M4 7 L6 9.5 L10 5"
                      fill="none"
                      stroke="white"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  )}
                  {d.decision === 'INVALID' && (
                    <g>
                      <line x1="4.5" y1="4.5" x2="9.5" y2="9.5" stroke="white" strokeWidth="1.8" strokeLinecap="round" />
                      <line x1="9.5" y1="4.5" x2="4.5" y2="9.5" stroke="white" strokeWidth="1.8" strokeLinecap="round" />
                    </g>
                  )}
                  {d.decision === 'NEEDS_REVIEW' && (
                    <g>
                      {/* Head */}
                      <circle cx="7" cy="5" r="1.8" fill="white" />
                      {/* Body */}
                      <path d="M3.5 11.5 Q3.5 8 7 8 Q10.5 8 10.5 11.5" fill="white" />
                    </g>
                  )}
                </svg>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
};

// Scanner legend - shows all scanners with their status
interface ScannerLegendProps {
  receipt: LabelEvaluatorReceipt;
  scannerState: ScannerState;
  revealedDecisions: RevealedDecision[];
}

const ScannerLegend: React.FC<ScannerLegendProps> = ({
  receipt,
  scannerState,
  revealedDecisions,
}) => {
  const { currency, metadata, geometric, financial } = receipt;
  const hasGeometricIssues = geometric.issues_found > 0;

  // Determine waiting states based on dependencies
  // Currency waits for Line Item
  const currencyIsWaiting = scannerState.lineItem < 100 && scannerState.currency === 0;
  // Financial waits for Currency + Metadata
  const currencyEndTime = scannerState.lineItem >= 100 ? scannerState.currency : 0;
  const financialIsWaiting = (currencyEndTime < 100 || scannerState.metadata < 100) && scannerState.financial === 0;
  // Review waits for Geometric (and only shows if review data exists)
  const hasReviewData = receipt.review !== null && receipt.review !== undefined;
  const reviewIsWaiting = hasReviewData && scannerState.geometric < 100 && scannerState.review === 0;

  // Filter decisions by scanner type
  const metadataDecisions = revealedDecisions.filter(d => d.type === 'metadata');
  const currencyDecisions = revealedDecisions.filter(d => d.type === 'currency');
  const financialDecisions = revealedDecisions.filter(d => d.type === 'financial');
  const reviewDecisions = revealedDecisions.filter(d => d.type === 'review');

  // Get review data (may be undefined if no geometric issues)
  const review = receipt.review;

  return (
    <div className={styles.scannerLegend}>
      {/* Blue - Independent */}
      <ScannerLegendItem
        name="Metadata"
        color={SCANNER_COLORS.metadata}
        progress={scannerState.metadata}
        isWaiting={false}
        isComplete={scannerState.metadata >= 100}
        durationMs={metadata.duration_seconds * 1000}
        decisions={metadataDecisions}
        totalDecisions={metadata.all_decisions.length}
      />
      {/* Orange - Geometric → Review chain */}
      <ScannerLegendItem
        name="Geometric"
        color={SCANNER_COLORS.geometric}
        progress={scannerState.geometric}
        isWaiting={false}
        isComplete={scannerState.geometric >= 100}
        durationMs={(geometric.duration_seconds || 0.1) * 1000}
      />
      <ScannerLegendItem
        name="Review"
        color={SCANNER_COLORS.review}
        progress={hasReviewData ? scannerState.review : 0}
        isWaiting={reviewIsWaiting}
        isComplete={hasReviewData && scannerState.review >= 100}
        isSkipped={!hasReviewData}
        durationMs={review ? review.duration_seconds * 1000 : undefined}
        decisions={reviewDecisions}
        totalDecisions={review ? review.all_decisions.length : 0}
      />
      {/* Purple - Line Item → Currency → Financial chain */}
      <ScannerLegendItem
        name="Line Item"
        color={SCANNER_COLORS.lineItem}
        progress={scannerState.lineItem}
        isWaiting={false}
        isComplete={scannerState.lineItem >= 100}
        durationMs={receipt.line_item_duration_seconds ? receipt.line_item_duration_seconds * 1000 : undefined}
      />
      <ScannerLegendItem
        name="Currency"
        color={SCANNER_COLORS.currency}
        progress={scannerState.currency}
        isWaiting={currencyIsWaiting}
        isComplete={scannerState.currency >= 100}
        durationMs={currency.duration_seconds * 1000}
        decisions={currencyDecisions}
        totalDecisions={currency.all_decisions.length}
      />
      <ScannerLegendItem
        name="Financial"
        color={SCANNER_COLORS.financial}
        progress={scannerState.financial}
        isWaiting={financialIsWaiting}
        isComplete={scannerState.financial >= 100}
        durationMs={financial.duration_seconds * 1000}
        decisions={financialDecisions}
        totalDecisions={financial.all_decisions.length}
      />
    </div>
  );
};

const LabelEvaluatorVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  const [receipts, setReceipts] = useState<LabelEvaluatorReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [phase, setPhase] = useState<Phase>("idle");
  const [scannerState, setScannerState] = useState<ScannerState>({
    lineItem: 0,
    metadata: 0,
    geometric: 0,
    financial: 0,
    currency: 0,
    review: 0,
  });
  const [revealedDecisions, setRevealedDecisions] = useState<RevealedDecision[]>([]);
  const [formatSupport, setFormatSupport] = useState<{
    supportsWebP: boolean;
    supportsAVIF: boolean;
  } | null>(null);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Fetch visualization data
  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await api.fetchLabelEvaluatorVisualization();
        if (response && response.receipts) {
          setReceipts(response.receipts);
        }
      } catch (err) {
        console.error("Failed to fetch label evaluator data:", err);
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const currentReceipt = receipts[currentIndex];

  // Calculate revealed decisions based on each scanner's progress
  useEffect(() => {
    if (!currentReceipt) return;

    const { words, currency, metadata, financial, review } = currentReceipt;
    const decisions: RevealedDecision[] = [];

    // Helper to check if a word's top edge has been passed by a scanner
    const isWordScanned = (lineId: number, wordId: number, progress: number) => {
      const word = words.find(w => w.line_id === lineId && w.word_id === wordId);
      if (!word) return false;
      const wordTopY = 1 - word.bbox.y - word.bbox.height;
      return wordTopY <= (progress / 100);
    };

    // Metadata decisions - track ALL decisions (V/I/R)
    metadata.all_decisions.forEach((d) => {
      if (isWordScanned(d.issue.line_id, d.issue.word_id, scannerState.metadata)) {
        const word = words.find(w => w.line_id === d.issue.line_id && w.word_id === d.issue.word_id);
        if (word) {
          decisions.push({
            key: `metadata_${d.issue.line_id}_${d.issue.word_id}`,
            type: 'metadata',
            decision: d.llm_review.decision as "VALID" | "INVALID" | "NEEDS_REVIEW",
            wordText: d.issue.word_text,
            lineId: d.issue.line_id,
            wordId: d.issue.word_id,
            bbox: word.bbox,
          });
        }
      }
    });

    // Currency decisions - track ALL decisions (V/I/R)
    currency.all_decisions.forEach((d) => {
      if (isWordScanned(d.issue.line_id, d.issue.word_id, scannerState.currency)) {
        const word = words.find(w => w.line_id === d.issue.line_id && w.word_id === d.issue.word_id);
        if (word) {
          decisions.push({
            key: `currency_${d.issue.line_id}_${d.issue.word_id}`,
            type: 'currency',
            decision: d.llm_review.decision as "VALID" | "INVALID" | "NEEDS_REVIEW",
            wordText: d.issue.word_text,
            lineId: d.issue.line_id,
            wordId: d.issue.word_id,
            bbox: word.bbox,
          });
        }
      }
    });

    // Financial decisions - track ALL decisions (V/I/R)
    financial.all_decisions.forEach((d) => {
      if (isWordScanned(d.issue.line_id, d.issue.word_id, scannerState.financial)) {
        const word = words.find(w => w.line_id === d.issue.line_id && w.word_id === d.issue.word_id);
        if (word) {
          decisions.push({
            key: `financial_${d.issue.line_id}_${d.issue.word_id}`,
            type: 'financial',
            decision: d.llm_review.decision as "VALID" | "INVALID" | "NEEDS_REVIEW",
            wordText: d.issue.word_text,
            lineId: d.issue.line_id,
            wordId: d.issue.word_id,
            bbox: word.bbox,
          });
        }
      }
    });

    // Review decisions - LLM decisions on geometrically-flagged words
    if (review) {
      review.all_decisions.forEach((d) => {
        if (isWordScanned(d.issue.line_id, d.issue.word_id, scannerState.review)) {
          const word = words.find(w => w.line_id === d.issue.line_id && w.word_id === d.issue.word_id);
          if (word) {
            decisions.push({
              key: `review_${d.issue.line_id}_${d.issue.word_id}`,
              type: 'review',
              decision: d.llm_review.decision as "VALID" | "INVALID" | "NEEDS_REVIEW",
              wordText: d.issue.word_text,
              lineId: d.issue.line_id,
              wordId: d.issue.word_id,
              bbox: word.bbox,
            });
          }
        }
      });
    }

    setRevealedDecisions(decisions);
  }, [currentReceipt, scannerState]);

  // Animation loop - each scanner progresses independently based on actual durations
  useEffect(() => {
    if (!inView || receipts.length === 0) {
      return;
    }

    if (isAnimatingRef.current) {
      return;
    }
    isAnimatingRef.current = true;

    const receipt = receipts[currentIndex];
    const hasGeometricIssues = receipt.geometric.issues_found > 0;

    // Get actual durations from the receipt data (in seconds)
    // Use line item duration from the merchant's pattern file, fallback to 2s estimate
    const rawLineItem = receipt.line_item_duration_seconds ?? 2;
    const rawMetadata = receipt.metadata.duration_seconds || 1;
    const rawGeometric = receipt.geometric.duration_seconds || 0.3;  // Deterministic, usually very fast
    const rawFinancial = receipt.financial.duration_seconds || 1;
    const rawCurrency = receipt.currency.duration_seconds || 1;
    // Review uses actual duration from data - only show if review actually ran
    // If review is null but hasGeometricIssues, it means review didn't run for this receipt
    const hasReviewData = receipt.review !== null && receipt.review !== undefined;
    const rawReview = hasReviewData ? (receipt.review!.duration_seconds || 1) : 0;

    // Calculate total raw duration to find scale factor
    // Branch 1: Line Item -> Currency -> Financial (sequential after Currency+Metadata)
    // Branch 2: Geometric -> Review (conditional, independent)
    const currencyEnd = rawLineItem + rawCurrency;
    const financialStart = Math.max(currencyEnd, rawMetadata);
    const branch1End = financialStart + rawFinancial;
    const branch2End = rawGeometric + (hasReviewData ? rawReview : 0);
    const totalRawDuration = Math.max(branch1End, branch2End);
    const scaleFactor = TARGET_TOTAL_DURATION / (totalRawDuration * 1000);

    // Scale each scanner's duration
    const lineItemDuration = Math.max(rawLineItem * 1000 * scaleFactor, MIN_PHASE_DURATION);
    const metadataDuration = Math.max(rawMetadata * 1000 * scaleFactor, MIN_PHASE_DURATION);
    const geometricDuration = Math.max(rawGeometric * 1000 * scaleFactor, MIN_PHASE_DURATION);
    const financialDuration = Math.max(rawFinancial * 1000 * scaleFactor, MIN_PHASE_DURATION);
    const currencyDuration = Math.max(rawCurrency * 1000 * scaleFactor, MIN_PHASE_DURATION);
    const reviewDuration = hasReviewData
      ? Math.max(rawReview * 1000 * scaleFactor, MIN_PHASE_DURATION)
      : 0;

    // Branch 1 timing: Line Item -> Currency, then Financial after Currency+Metadata
    const currencyStartTime = lineItemDuration;
    const currencyEndTime = currencyStartTime + currencyDuration;
    const financialStartTime = Math.max(currencyEndTime, metadataDuration);

    // Branch 2 timing: Geometric -> Review (independent of Branch 1)
    const reviewStartTime = geometricDuration;

    // Calculate total animation time (longest branch)
    const branch1EndTime = financialStartTime + financialDuration;
    const branch2EndTime = hasReviewData ? reviewStartTime + reviewDuration : geometricDuration;
    const allScannersEnd = Math.max(branch1EndTime, branch2EndTime);
    const holdEnd = allScannersEnd + HOLD_DURATION;
    const totalCycle = holdEnd + TRANSITION_DURATION;

    let receiptIdx = currentIndex;
    let startTime = performance.now();

    setPhase("scanning");
    setScannerState({
      lineItem: 0,
      metadata: 0,
      geometric: 0,
      financial: 0,
      currency: 0,
      review: 0,
    });

    const animate = (time: number) => {
      const elapsed = time - startTime;

      if (elapsed < allScannersEnd) {
        // Branch 1: Line Item, Metadata start at t=0
        const lineItemProgress = Math.min((elapsed / lineItemDuration) * 100, 100);
        const metadataProgress = Math.min((elapsed / metadataDuration) * 100, 100);

        // Branch 1: Currency starts after Line Item completes
        let currencyProgress = 0;
        if (elapsed >= currencyStartTime) {
          const currencyElapsed = elapsed - currencyStartTime;
          currencyProgress = Math.min((currencyElapsed / currencyDuration) * 100, 100);
        }

        // Branch 1: Financial starts after Currency + Metadata complete
        let financialProgress = 0;
        if (elapsed >= financialStartTime) {
          const financialElapsed = elapsed - financialStartTime;
          financialProgress = Math.min((financialElapsed / financialDuration) * 100, 100);
        }

        // Branch 2: Geometric starts at t=0
        const geometricProgress = Math.min((elapsed / geometricDuration) * 100, 100);

        // Branch 2: Review starts after Geometric completes (if review data exists)
        let reviewProgress = 0;
        if (hasReviewData && elapsed >= reviewStartTime) {
          const reviewElapsed = elapsed - reviewStartTime;
          reviewProgress = Math.min((reviewElapsed / reviewDuration) * 100, 100);
        }

        setPhase("scanning");
        setScannerState({
          lineItem: lineItemProgress,
          metadata: metadataProgress,
          geometric: geometricProgress,
          financial: financialProgress,
          currency: currencyProgress,
          review: reviewProgress,
        });
      } else if (elapsed < holdEnd) {
        // Hold phase - show complete state
        setPhase("complete");
        setScannerState({
          lineItem: 100,
          metadata: 100,
          geometric: 100,
          financial: 100,
          currency: 100,
          review: hasReviewData ? 100 : 0,
        });
      } else if (elapsed < totalCycle) {
        // Transition phase - keep showing complete
      } else {
        // Move to next receipt
        receiptIdx = (receiptIdx + 1) % receipts.length;
        setCurrentIndex(receiptIdx);

        // Reset for new receipt
        setPhase("scanning");
        setScannerState({
          lineItem: 0,
          metadata: 0,
          geometric: 0,
          financial: 0,
          currency: 0,
          review: 0,
        });
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
        Loading label evaluator data...
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
        No label evaluator data available
      </div>
    );
  }

  return (
    <div ref={ref} className={styles.container}>
      <div className={styles.mainWrapper}>
        <ReceiptViewer
          receipt={currentReceipt}
          scannerState={scannerState}
          phase={phase}
          revealedDecisions={revealedDecisions}
          formatSupport={formatSupport}
        />
        <ScannerLegend
          receipt={currentReceipt}
          scannerState={scannerState}
          revealedDecisions={revealedDecisions}
        />
      </div>
      <div className={styles.receiptCounter}>
        Receipt {currentIndex + 1} of {receipts.length}
      </div>
    </div>
  );
};

export default LabelEvaluatorVisualization;
