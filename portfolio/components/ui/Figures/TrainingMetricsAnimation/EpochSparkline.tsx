import React, { useCallback, useEffect, useState, useMemo, useRef } from "react";
import {
  TrainingMetricsEpoch,
  TrainingSynthesisSummary,
} from "../../../../types/api";
import styles from "./TrainingMetricsAnimation.module.css";
import {
  axisLabelAnchor,
  buildMetricPath,
  clickXToEpochIndex,
  computeAxisLabels,
  computeScales,
  countMetricValues,
} from "./sparkline";
import { SynthesisEvidenceStrip } from "./SynthesisEvidence";

// Spring config for smooth animations
export const SPRING_CONFIG = { tension: 120, friction: 14 };

// Cap the total autoplay duration so a 60+-epoch run finishes in a
// reasonable time rather than ~75s at 1200ms/step.
export const TIMELINE_AUTOPLAY_TOTAL_MS = 18000;
export const TIMELINE_AUTOPLAY_MIN_STEP_MS = 250;
export const TIMELINE_AUTOPLAY_MAX_STEP_MS = 1200;

// Epoch Sparkline: a line chart of val_f1 across all epochs. Replaces
// the per-epoch dot row which couldn't scale past ~20 epochs and only
// encoded "which epoch you're on" — the sparkline additionally encodes
// the convergence shape, plateau, and any drift the user can read at a
// glance. Click anywhere on the curve to scrub.
export interface EpochSparklineProps {
  epochs: TrainingMetricsEpoch[];
  currentIndex: number;
  onSelectEpoch: (index: number) => void;
  showBestLabel: boolean;
  synthesis?: TrainingSynthesisSummary | null;
}

// SVG viewBox dims (internal coordinates). The viewBox width tracks the
// rendered container width (via ResizeObserver) so the curve isn't
// horizontally stretched by `preserveAspectRatio="none"` — a 7%-or-so
// distortion would otherwise make the early-epoch climb look gentler
// than it really is.
export const SVG_W_FALLBACK = 600; // before measurement / SSR
export const SVG_H = 118;
export const F1_CHART_H = 76;
export const SVG_PAD_X = 8; // left/right inset so end markers don't clip
export const SVG_PAD_TOP = 18; // room for "BEST" label
export const F1_PAD_BOTTOM = 8;
export const LOSS_STRIP_TOP = 86;
export const LOSS_STRIP_BOTTOM = 104;
export const AXIS_LABEL_MIN_GAP_PX = 24; // min viewBox-x distance before suppressing best label

export const SPARKLINE_DIMS_FALLBACK = {
  width: SVG_W_FALLBACK,
  height: F1_CHART_H,
  padX: SVG_PAD_X,
  padTop: SVG_PAD_TOP,
  padBottom: F1_PAD_BOTTOM,
};

export const EpochSparkline: React.FC<EpochSparklineProps> = ({
  epochs,
  currentIndex,
  onSelectEpoch,
  showBestLabel,
  synthesis,
}) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const [svgWidth, setSvgWidth] = useState<number>(SVG_W_FALLBACK);

  // Track rendered SVG width so the viewBox aspect matches the screen
  // aspect and the curve isn't horizontally stretched.
  useEffect(() => {
    const node = svgRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.round(entry.contentRect.width);
        if (w > 0) setSvgWidth(w);
      }
    });
    ro.observe(node);
    return () => ro.disconnect();
  }, []);

  const dims = useMemo(
    () => ({ ...SPARKLINE_DIMS_FALLBACK, width: svgWidth }),
    [svgWidth]
  );

  const ratioMetricKeys = useMemo(
    () =>
      (["val_f1", "val_precision", "val_recall"] as const).filter(
        (metricKey) => countMetricValues(epochs, metricKey) >= 2
      ),
    [epochs]
  );

  const { points, yScale, xScale, dataMin, dataMax } = useMemo(
    () => computeScales(epochs, dims, [...ratioMetricKeys]),
    [epochs, dims, ratioMetricKeys]
  );
  const bestIdx = useMemo(() => epochs.findIndex((e) => e.is_best), [epochs]);
  const precisionPath = useMemo(
    () => buildMetricPath(epochs, "val_precision", xScale, yScale),
    [epochs, xScale, yScale]
  );
  const recallPath = useMemo(
    () => buildMetricPath(epochs, "val_recall", xScale, yScale),
    [epochs, xScale, yScale]
  );

  const lossPaths = useMemo(() => {
    const values = epochs.flatMap((epoch) =>
      [epoch.metrics?.train_loss, epoch.metrics?.eval_loss].filter(
        (value): value is number => Number.isFinite(value)
      )
    );
    if (values.length === 0) {
      return null;
    }

    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min;
    const pad = span === 0 ? Math.max(0.01, max * 0.05) : span * 0.08;
    const dataMinLoss = Math.max(0, min - pad);
    const dataMaxLoss = max + pad;
    const yLossScale = (value: number) =>
      LOSS_STRIP_BOTTOM -
      ((value - dataMinLoss) / (dataMaxLoss - dataMinLoss || 1)) *
        (LOSS_STRIP_BOTTOM - LOSS_STRIP_TOP);

    return {
      train: buildMetricPath(epochs, "train_loss", xScale, yLossScale),
      eval: buildMetricPath(epochs, "eval_loss", xScale, yLossScale),
      hasTrain: countMetricValues(epochs, "train_loss") >= 2,
      hasEval: countMetricValues(epochs, "eval_loss") >= 2,
    };
  }, [epochs, xScale]);

  const handleClick = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      if (epochs.length === 0 || !svgRef.current) return;
      const rect = svgRef.current.getBoundingClientRect();
      const idx = clickXToEpochIndex(
        e.clientX,
        rect.left,
        rect.width,
        svgWidth,
        SVG_PAD_X,
        epochs.length
      );
      onSelectEpoch(idx);
    },
    [epochs.length, onSelectEpoch, svgWidth]
  );

  // Keyboard navigation — required because role="slider" promises AT users
  // they can change the value (WCAG). Arrow keys step by 1, Home/End jump
  // to first/last.
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<SVGSVGElement>) => {
      if (epochs.length === 0) return;
      const last = epochs.length - 1;
      let next = currentIndex;
      switch (e.key) {
        case "ArrowLeft":
        case "ArrowDown":
          next = Math.max(0, currentIndex - 1);
          break;
        case "ArrowRight":
        case "ArrowUp":
          next = Math.min(last, currentIndex + 1);
          break;
        case "PageDown":
          next = Math.max(0, currentIndex - Math.max(1, Math.floor(last / 10)));
          break;
        case "PageUp":
          next = Math.min(
            last,
            currentIndex + Math.max(1, Math.floor(last / 10))
          );
          break;
        case "Home":
          next = 0;
          break;
        case "End":
          next = last;
          break;
        default:
          return;
      }
      e.preventDefault();
      if (next !== currentIndex) onSelectEpoch(next);
    },
    [epochs.length, currentIndex, onSelectEpoch]
  );

  const currentEpoch = epochs[currentIndex];
  const currentF1 = currentEpoch?.metrics?.val_f1 ?? 0;
  const currentPrecision = currentEpoch?.metrics?.val_precision;
  const currentRecall = currentEpoch?.metrics?.val_recall;
  const currentTrainLoss = currentEpoch?.metrics?.train_loss;
  const currentEvalLoss = currentEpoch?.metrics?.eval_loss;
  const cx = xScale(currentIndex);
  const cy = yScale(currentF1);
  const bestEpoch = bestIdx >= 0 ? epochs[bestIdx] : null;
  const bestF1 = bestEpoch?.metrics?.val_f1 ?? 0;
  const bx = bestIdx >= 0 ? xScale(bestIdx) : 0;
  const by = bestIdx >= 0 ? yScale(bestF1) : 0;
  const lastIdx = epochs.length - 1;
  const showBest = bestIdx >= 0 && showBestLabel;
  const showPostBestRegion = bestIdx >= 0 && bestIdx < lastIdx;
  const postBestWidth = showPostBestRegion
    ? Math.max(0, svgWidth - SVG_PAD_X - bx)
    : 0;

  const axisLabels = useMemo(
    () => computeAxisLabels(epochs, bestIdx, xScale, AXIS_LABEL_MIN_GAP_PX),
    [epochs, bestIdx, xScale]
  );
  void dataMin;
  void dataMax;

  const ariaMetricText = [
    `F1 ${currentF1.toFixed(3)}`,
    Number.isFinite(currentPrecision)
      ? `precision ${(currentPrecision as number).toFixed(3)}`
      : null,
    Number.isFinite(currentRecall)
      ? `recall ${(currentRecall as number).toFixed(3)}`
      : null,
    Number.isFinite(currentTrainLoss)
      ? `train loss ${(currentTrainLoss as number).toFixed(3)}`
      : null,
    Number.isFinite(currentEvalLoss)
      ? `eval loss ${(currentEvalLoss as number).toFixed(3)}`
      : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <>
      {/* Desktop sparkline */}
      <div className={styles.timeline}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${svgWidth} ${SVG_H}`}
          preserveAspectRatio="none"
          className={styles.sparkline}
          onClick={handleClick}
          onKeyDown={handleKeyDown}
          tabIndex={0}
          role="slider"
          aria-label={`Training epoch — use arrow keys to scrub, Home/End to jump to first/last`}
          aria-valuemin={0}
          aria-valuemax={lastIdx}
          aria-valuenow={currentIndex}
          aria-valuetext={
            currentEpoch
              ? `Epoch ${currentEpoch.epoch}${
                  currentIndex === bestIdx ? " (best)" : ""
                }, ${ariaMetricText}`
              : undefined
          }
        >
          <title>
            Training metrics by epoch. Solid line is F1, dashed line is
            precision, dotted line is recall, and the lower strip shows train
            and evaluation loss.
          </title>

          {showPostBestRegion && (
            <rect
              x={bx}
              y={SVG_PAD_TOP - 4}
              width={postBestWidth}
              height={LOSS_STRIP_BOTTOM - SVG_PAD_TOP + 4}
              className={styles.sparklinePostBestRegion}
            >
              <title>
                Epochs after the best validation F1. Watch for train loss
                falling while validation metrics stop improving.
              </title>
            </rect>
          )}

          <g className={styles.sparklineMetricLegend} aria-hidden="true">
            <line x1={SVG_PAD_X} x2={SVG_PAD_X + 15} y1={8} y2={8} />
            <text x={SVG_PAD_X + 19} y={11}>
              F1
            </text>
            <line
              x1={SVG_PAD_X + 45}
              x2={SVG_PAD_X + 60}
              y1={8}
              y2={8}
              className={styles.sparklinePrecisionSample}
            />
            <text x={SVG_PAD_X + 64} y={11}>
              P
            </text>
            <line
              x1={SVG_PAD_X + 84}
              x2={SVG_PAD_X + 99}
              y1={8}
              y2={8}
              className={styles.sparklineRecallSample}
            />
            <text x={SVG_PAD_X + 103} y={11}>
              R
            </text>
          </g>

          {/* Convergence curve */}
          {precisionPath && (
            <path
              d={precisionPath}
              className={styles.sparklinePrecisionPath}
              vectorEffect="non-scaling-stroke"
            />
          )}

          {recallPath && (
            <path
              d={recallPath}
              className={styles.sparklineRecallPath}
              vectorEffect="non-scaling-stroke"
            />
          )}

          {epochs.length >= 2 && (
            <polyline
              points={points}
              className={styles.sparklinePath}
              vectorEffect="non-scaling-stroke"
            />
          )}

          {/* Vertical drop line at active epoch */}
          <line
            x1={cx}
            x2={cx}
            y1={cy}
            y2={LOSS_STRIP_BOTTOM}
            className={styles.sparklineActiveLine}
            vectorEffect="non-scaling-stroke"
          />

          <line
            x1={SVG_PAD_X}
            x2={svgWidth - SVG_PAD_X}
            y1={LOSS_STRIP_TOP - 7}
            y2={LOSS_STRIP_TOP - 7}
            className={styles.sparklineLossDivider}
            vectorEffect="non-scaling-stroke"
          />

          {lossPaths && (lossPaths.hasTrain || lossPaths.hasEval) && (
            <g className={styles.sparklineLossGroup}>
              {lossPaths.hasEval && (
                <path
                  d={lossPaths.eval}
                  className={styles.sparklineEvalLossPath}
                  vectorEffect="non-scaling-stroke"
                />
              )}
              {lossPaths.hasTrain && (
                <path
                  d={lossPaths.train}
                  className={styles.sparklineTrainLossPath}
                  vectorEffect="non-scaling-stroke"
                />
              )}
              <text
                x={SVG_PAD_X}
                y={LOSS_STRIP_TOP + 3}
                className={styles.sparklineLossLabel}
              >
                loss
              </text>
            </g>
          )}

          {/* Best epoch marker (open ring under the curve, labeled) */}
          {bestIdx >= 0 && (
            <>
              {showBest && (
                <text
                  x={bx}
                  y={SVG_PAD_TOP - 6}
                  className={styles.sparklineBestLabel}
                  textAnchor="middle"
                >
                  BEST
                </text>
              )}
              <circle
                cx={bx}
                cy={by}
                r={4}
                className={styles.sparklineBestMarker}
                vectorEffect="non-scaling-stroke"
              >
                <title>
                  {bestEpoch
                    ? `Best epoch ${bestEpoch.epoch} · F1 ${bestF1.toFixed(3)}`
                    : ""}
                </title>
              </circle>
            </>
          )}

          {/* Active epoch marker — filled dot on the curve */}
          <circle
            cx={cx}
            cy={cy}
            r={4.5}
            className={styles.sparklineActiveMarker}
            vectorEffect="non-scaling-stroke"
          />

          {/* Axis labels (first/best/last epoch numbers) */}
          {axisLabels.map((l) => (
            <text
              key={l.key}
              x={l.x}
              y={SVG_H - 3}
              className={styles.sparklineAxisLabel}
              textAnchor={axisLabelAnchor(l.x, svgWidth, SVG_PAD_X)}
            >
              {l.text}
            </text>
          ))}
        </svg>
      </div>

      {/* Mobile uses the existing prev/next arrow UI — touch-friendly,
          handles any epoch count naturally. */}
      <div className={styles.timelineMobile}>
        <button
          className={styles.timelineArrow}
          onClick={() => onSelectEpoch(Math.max(0, currentIndex - 1))}
          disabled={currentIndex === 0}
          aria-label="Previous epoch"
        >
          ‹
        </button>
        <div className={styles.timelineMobileCenter}>
          {showBest && currentIndex === bestIdx && (
            <span className={styles.timelineBestLabelMobile}>Best</span>
          )}
          <span className={styles.timelineMobileText}>
            {currentIndex + 1} / {epochs.length}
          </span>
        </div>
        <button
          className={styles.timelineArrow}
          onClick={() => onSelectEpoch(Math.min(epochs.length - 1, currentIndex + 1))}
          disabled={currentIndex === epochs.length - 1}
          aria-label="Next epoch"
        >
          ›
        </button>
      </div>
      <SynthesisEvidenceStrip synthesis={synthesis} />
    </>
  );
};
