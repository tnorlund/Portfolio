import React, { useMemo } from "react";
import { animated, useSpring } from "@react-spring/web";
import { LabelPairPattern, FlaggedWordInfo } from "./mockData";
import styles from "./GeometricAnomalyVisualization.module.css";

interface PatternScatterPlotProps {
  pattern: LabelPairPattern | null;
  flaggedWord: FlaggedWordInfo | null;
  animationProgress: number; // 0-1
  allPatterns: LabelPairPattern[];
  selectedPatternIndex: number;
  onSelectPattern: (index: number) => void;
}

const PatternScatterPlot: React.FC<PatternScatterPlotProps> = ({
  pattern,
  flaggedWord,
  animationProgress,
  allPatterns,
  selectedPatternIndex,
  onSelectPattern,
}) => {
  // SVG dimensions and padding
  const width = 350;
  const height = 300;
  const padding = 40;
  const plotWidth = width - padding * 2;
  const plotHeight = height - padding * 2;

  // Calculate scale based on pattern data
  const { scaleX, scaleY, centerX, centerY } = useMemo(() => {
    if (!pattern) {
      return {
        scaleX: 1,
        scaleY: 1,
        centerX: width / 2,
        centerY: height / 2,
      };
    }

    // Find data bounds
    const allDx = pattern.observations.map((o) => o.dx);
    const allDy = pattern.observations.map((o) => o.dy);

    if (flaggedWord) {
      allDx.push(flaggedWord.actual.dx, flaggedWord.expected.dx);
      allDy.push(flaggedWord.actual.dy, flaggedWord.expected.dy);
    }

    const maxAbsDx = Math.max(...allDx.map(Math.abs), 0.1);
    const maxAbsDy = Math.max(...allDy.map(Math.abs), 0.1);

    // Add 20% margin
    const rangeX = maxAbsDx * 2.4;
    const rangeY = maxAbsDy * 2.4;

    return {
      scaleX: plotWidth / rangeX,
      scaleY: plotHeight / rangeY,
      centerX: padding + plotWidth / 2,
      centerY: padding + plotHeight / 2,
    };
  }, [pattern, flaggedWord, plotWidth, plotHeight, padding]);

  // Transform data coordinates to SVG coordinates
  const toSvgX = (dx: number) => centerX + dx * scaleX;
  const toSvgY = (dy: number) => centerY + dy * scaleY; // Y increases downward in SVG

  // Animation springs
  const ellipseSpring = useSpring({
    opacity: animationProgress > 0.3 ? 1 : 0,
    scale: animationProgress > 0.3 ? 1 : 0,
    config: { tension: 120, friction: 14 },
  });

  const dotsSpring = useSpring({
    opacity: animationProgress > 0.5 ? 0.6 : 0,
    config: { tension: 120, friction: 14 },
  });

  const actualPointSpring = useSpring({
    opacity: animationProgress > 0.7 ? 1 : 0,
    scale: animationProgress > 0.7 ? 1 : 0,
    config: { tension: 200, friction: 15 },
  });

  if (!pattern) {
    return (
      <div className={styles.patternPanel}>
        <h4 className={styles.panelTitle}>Pattern View</h4>
        <div className={styles.patternContainer}>
          <p style={{ opacity: 0.5, fontSize: "0.9rem" }}>
            Select a pattern to visualize
          </p>
        </div>
      </div>
    );
  }

  // Calculate ellipse radii for confidence intervals
  const ellipse1Radius = pattern.std * 1.5 * scaleX; // 1.5 sigma
  const ellipse2Radius = pattern.std * 2.0 * scaleX; // 2.0 sigma
  const ellipse3Radius = pattern.std * 2.5 * scaleX; // 2.5 sigma

  const meanX = toSvgX(pattern.mean.dx);
  const meanY = toSvgY(pattern.mean.dy);

  return (
    <div className={styles.patternPanel}>
      <h4 className={styles.panelTitle}>Geometric Pattern</h4>

      {/* Pattern selector */}
      <div className={styles.patternSelector}>
        {allPatterns.map((p, index) => (
          <button
            key={`${p.from}-${p.to}`}
            className={`${styles.patternButton} ${
              index === selectedPatternIndex ? styles.patternButtonActive : ""
            }`}
            onClick={() => onSelectPattern(index)}
          >
            {p.from} → {p.to}
          </button>
        ))}
      </div>

      <div className={styles.patternContainer}>
        <svg className={styles.patternSvg} viewBox={`0 0 ${width} ${height}`}>
          {/* Arrow marker definition */}
          <defs>
            <marker
              id="arrowhead"
              markerWidth="10"
              markerHeight="7"
              refX="9"
              refY="3.5"
              orient="auto"
            >
              <polygon
                points="0 0, 10 3.5, 0 7"
                fill="var(--color-red)"
              />
            </marker>
          </defs>

          {/* Grid lines */}
          <line
            x1={padding}
            y1={centerY}
            x2={width - padding}
            y2={centerY}
            stroke="var(--text-color)"
            strokeOpacity={0.1}
            strokeDasharray="4,4"
          />
          <line
            x1={centerX}
            y1={padding}
            x2={centerX}
            y2={height - padding}
            stroke="var(--text-color)"
            strokeOpacity={0.1}
            strokeDasharray="4,4"
          />

          {/* Axis labels */}
          <text
            x={width - padding + 5}
            y={centerY + 4}
            className={styles.axisLabel}
            textAnchor="start"
          >
            dx →
          </text>
          <text
            x={centerX - 4}
            y={height - padding + 15}
            className={styles.axisLabel}
            textAnchor="middle"
          >
            dy ↓
          </text>

          {/* Confidence ellipses (3σ, 2σ, 1.5σ) */}
          <animated.g
            style={{
              opacity: ellipseSpring.opacity,
              transform: ellipseSpring.scale.to(
                (s) => `scale(${s})`
              ),
              transformOrigin: `${meanX}px ${meanY}px`,
            }}
          >
            <ellipse
              cx={meanX}
              cy={meanY}
              rx={ellipse3Radius}
              ry={ellipse3Radius}
              className={`${styles.confidenceEllipse} ${styles.ellipse3}`}
            />
            <ellipse
              cx={meanX}
              cy={meanY}
              rx={ellipse2Radius}
              ry={ellipse2Radius}
              className={`${styles.confidenceEllipse} ${styles.ellipse2}`}
            />
            <ellipse
              cx={meanX}
              cy={meanY}
              rx={ellipse1Radius}
              ry={ellipse1Radius}
              className={`${styles.confidenceEllipse} ${styles.ellipse1}`}
            />
          </animated.g>

          {/* Training observations */}
          <animated.g style={{ opacity: dotsSpring.opacity }}>
            {pattern.observations.map((obs, i) => (
              <circle
                key={i}
                cx={toSvgX(obs.dx)}
                cy={toSvgY(obs.dy)}
                r={3}
                className={styles.observationDot}
              />
            ))}
          </animated.g>

          {/* Mean point */}
          <animated.circle
            cx={meanX}
            cy={meanY}
            r={8}
            className={styles.meanPoint}
            style={{
              opacity: ellipseSpring.opacity,
              transform: ellipseSpring.scale.to((s) => `scale(${s})`),
              transformOrigin: `${meanX}px ${meanY}px`,
            }}
          />

          {/* Deviation vector and actual point (if flagged) */}
          {flaggedWord && (
            <>
              {/* Deviation vector */}
              <animated.line
                x1={toSvgX(flaggedWord.expected.dx)}
                y1={toSvgY(flaggedWord.expected.dy)}
                x2={toSvgX(flaggedWord.actual.dx)}
                y2={toSvgY(flaggedWord.actual.dy)}
                className={styles.deviationVector}
                style={{ opacity: actualPointSpring.opacity }}
              />

              {/* Actual point */}
              <animated.circle
                cx={toSvgX(flaggedWord.actual.dx)}
                cy={toSvgY(flaggedWord.actual.dy)}
                r={8}
                className={styles.actualPoint}
                style={{
                  opacity: actualPointSpring.opacity,
                  transform: actualPointSpring.scale.to((s) => `scale(${s})`),
                  transformOrigin: `${toSvgX(flaggedWord.actual.dx)}px ${toSvgY(flaggedWord.actual.dy)}px`,
                }}
              />
            </>
          )}
        </svg>
      </div>

      {/* Legend */}
      <div className={styles.patternLegend}>
        <div className={styles.legendItem}>
          <span
            className={styles.legendDot}
            style={{ background: "var(--color-green)" }}
          />
          <span>Expected (mean)</span>
        </div>
        <div className={styles.legendItem}>
          <span
            className={styles.legendDot}
            style={{ background: "var(--color-red)" }}
          />
          <span>Actual (flagged)</span>
        </div>
        <div className={styles.legendItem}>
          <span
            className={styles.legendLine}
            style={{ background: "var(--color-green)", opacity: 0.8 }}
          />
          <span>1.5σ threshold</span>
        </div>
        <div className={styles.legendItem}>
          <span
            className={styles.legendLine}
            style={{ background: "var(--color-yellow)", opacity: 0.6 }}
          />
          <span>2.0σ threshold</span>
        </div>
      </div>

      {/* Info panel with metrics */}
      {flaggedWord && animationProgress > 0.8 && (
        <div className={styles.infoPanel}>
          <div className={styles.infoPanelTitle}>Anomaly Detection</div>
          <div className={styles.infoPanelContent}>
            The word&apos;s position deviates from the expected pattern.
            <br />
            Z-score:{" "}
            <span className={`${styles.metric} ${styles.metricBad}`}>
              {flaggedWord.zScore.toFixed(2)}
            </span>
            {" > "}
            Threshold:{" "}
            <span className={`${styles.metric} ${styles.metricGood}`}>
              {flaggedWord.threshold.toFixed(1)}σ
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default PatternScatterPlot;
