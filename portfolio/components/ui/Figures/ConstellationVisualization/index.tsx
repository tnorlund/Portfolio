import React, { useState, useEffect, useRef } from "react";
import { useInView } from "react-intersection-observer";
import { animated, useSpring } from "@react-spring/web";
import { ConstellationData, ConstellationWord } from "./types";
import { api } from "../../../../services/api";
import { GeometricAnomalyCacheResponse } from "../../../../types/api";
import styles from "./ConstellationVisualization.module.css";

// Label colors
const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "#f59e0b",
  ADDRESS_LINE: "#8b5cf6",
  PHONE_NUMBER: "#8b5cf6",
  DATE: "#3b82f6",
  TIME: "#3b82f6",
  PAYMENT_METHOD: "#10b981",
  PRODUCT_NAME: "#6b7280",
  LINE_TOTAL: "#10b981",
  SUBTOTAL: "#10b981",
  TAX: "#ef4444",
  GRAND_TOTAL: "#10b981",
};

/**
 * Transform API response to the component's internal data format
 */
function transformApiResponse(response: GeometricAnomalyCacheResponse): ConstellationData | null {
  // Check if constellation data exists
  if (!response.constellation || !response.constellation.labels.length) {
    return null;
  }

  const constellationLabels = new Set(response.constellation.labels);
  const flaggedLabel = response.constellation_anomaly?.flagged_label || null;

  // Build label centroids for centroid calculation
  const labelPositions: Record<string, { x: number; y: number; count: number }> = {};
  for (const word of response.receipt.words) {
    if (word.label && constellationLabels.has(word.label)) {
      if (!labelPositions[word.label]) {
        labelPositions[word.label] = { x: 0, y: 0, count: 0 };
      }
      labelPositions[word.label].x += word.x + word.width / 2;
      labelPositions[word.label].y += word.y + word.height / 2;
      labelPositions[word.label].count++;
    }
  }

  // Calculate centroid from label centers
  let totalX = 0;
  let totalY = 0;
  let count = 0;
  for (const label of response.constellation.labels) {
    const pos = labelPositions[label];
    if (pos && pos.count > 0) {
      totalX += pos.x / pos.count;
      totalY += pos.y / pos.count;
      count++;
    }
  }
  const centroid = count > 0
    ? { x: totalX / count, y: totalY / count }
    : { x: 0.5, y: 0.5 };

  // Transform words
  const words: ConstellationWord[] = response.receipt.words.map((w) => ({
    id: `${w.line_id}-${w.word_id}`,
    text: w.text,
    x: w.x,
    y: w.y,
    width: w.width,
    height: w.height,
    label: w.label,
    isInConstellation: w.label ? constellationLabels.has(w.label) : false,
    isFlagged: w.label === flaggedLabel && w.is_flagged,
  }));

  // Build members with actual positions
  const members = response.constellation.members.map((m) => {
    const isThisFlagged = m.label === flaggedLabel;
    const defaultOffset = { dx: 0, dy: 0 };
    return {
      label: m.label,
      expected: m.expected || defaultOffset,
      actual: isThisFlagged && response.constellation_anomaly
        ? response.constellation_anomaly.actual
        : m.actual || m.expected || defaultOffset,
      isFlagged: isThisFlagged,
      deviation: isThisFlagged && response.constellation_anomaly
        ? response.constellation_anomaly.deviation
        : undefined,
    };
  });

  return {
    receipt: {
      imageId: response.receipt.image_id,
      receiptId: response.receipt.receipt_id,
      merchantName: response.receipt.merchant_name,
      words,
    },
    constellation: {
      labels: response.constellation.labels,
      centroid,
      members,
      flaggedLabel: flaggedLabel || "",
      reasoning: response.constellation_anomaly?.reasoning ||
        `Constellation [${response.constellation.labels.join(" + ")}] detected.`,
    },
  };
}

const ConstellationVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: true,
  });

  const [data, setData] = useState<ConstellationData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showConstellation, setShowConstellation] = useState(false);
  const [showExpected, setShowExpected] = useState(false);
  const [showDeviation, setShowDeviation] = useState(false);
  const hasStartedAnimation = useRef(false);

  // Fetch real data on mount
  useEffect(() => {
    api
      .fetchGeometricAnomaly()
      .then((response) => {
        const transformed = transformApiResponse(response);
        if (transformed) {
          setData(transformed);
        } else {
          setError("No constellation data in response");
        }
      })
      .catch((err) => {
        console.error("Failed to fetch constellation data:", err);
        setError("Failed to load data");
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, []);

  // Animation sequence when component comes into view and data is loaded
  useEffect(() => {
    if (!inView || !data || hasStartedAnimation.current) return;

    hasStartedAnimation.current = true;
    const timer1 = setTimeout(() => setShowConstellation(true), 500);
    const timer2 = setTimeout(() => setShowExpected(true), 1500);
    const timer3 = setTimeout(() => setShowDeviation(true), 2500);

    return () => {
      clearTimeout(timer1);
      clearTimeout(timer2);
      clearTimeout(timer3);
    };
  }, [inView, data]);

  // Animation springs
  const centroidSpring = useSpring({
    opacity: showConstellation ? 1 : 0,
    config: { tension: 200, friction: 20 },
  });

  const expectedSpring = useSpring({
    opacity: showExpected ? 0.6 : 0,
    config: { tension: 150, friction: 20 },
  });

  const deviationSpring = useSpring({
    opacity: showDeviation ? 1 : 0,
    config: { tension: 150, friction: 20 },
  });

  // Show loading state
  if (isLoading) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loadingState}>Loading constellation data...</div>
      </div>
    );
  }

  // Show error state
  if (error || !data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.errorState}>{error || "No constellation data available"}</div>
      </div>
    );
  }

  const { centroid, members, labels } = data.constellation;

  return (
    <div ref={ref} className={styles.container}>
      {/* Header */}
      <div className={styles.header}>
        <h4 className={styles.title}>Constellation Pattern Detection</h4>
        <div className={styles.constellationBadge}>
          {labels.join(" + ")}
        </div>
      </div>

      {/* Main visualization */}
      <div className={styles.visualizationContainer}>
        {/* Receipt panel */}
        <div className={styles.receiptPanel}>
          <div className={styles.panelTitle}>Receipt View</div>
          <div className={styles.receiptContainer}>
            {/* Words */}
            {data.receipt.words.map((word) => (
              <WordBox
                key={word.id}
                word={word}
                showHighlight={showConstellation && word.isInConstellation}
              />
            ))}

            {/* Constellation overlay */}
            {showConstellation && (
              <svg className={styles.constellationOverlay}>
                {/* Lines connecting constellation members to centroid */}
                {members.map((member) => {
                  const actualX = (centroid.x + member.actual.dx) * 100;
                  const actualY = (centroid.y + member.actual.dy) * 100;
                  return (
                    <animated.line
                      key={member.label}
                      x1={`${centroid.x * 100}%`}
                      y1={`${centroid.y * 100}%`}
                      x2={`${actualX}%`}
                      y2={`${actualY}%`}
                      stroke="#8b5cf6"
                      strokeWidth={1}
                      strokeDasharray="4,4"
                      style={{ opacity: centroidSpring.opacity }}
                    />
                  );
                })}

                {/* Expected position circle - only for flagged member */}
                {showExpected && members.filter(m => m.isFlagged).map((member) => {
                  const expectedX = (centroid.x + member.expected.dx) * 100;
                  const expectedY = (centroid.y + member.expected.dy) * 100;
                  return (
                    <animated.g key={`expected-${member.label}`} style={{ opacity: expectedSpring.opacity }}>
                      {/* Expected position marker */}
                      <circle
                        cx={`${expectedX}%`}
                        cy={`${expectedY}%`}
                        r={12}
                        fill="none"
                        stroke="#10b981"
                        strokeWidth={2}
                        strokeDasharray="6,3"
                      />
                      {/* Small label */}
                      <text
                        x={`${expectedX}%`}
                        y={`${expectedY - 2.5}%`}
                        textAnchor="middle"
                        fontSize="10"
                        fill="#10b981"
                      >
                        expected
                      </text>
                    </animated.g>
                  );
                })}

                {/* Deviation arrow from expected to actual */}
                {showDeviation && members.filter(m => m.isFlagged).map((member) => {
                  const expectedX = (centroid.x + member.expected.dx) * 100;
                  const expectedY = (centroid.y + member.expected.dy) * 100;
                  const actualX = (centroid.x + member.actual.dx) * 100;
                  const actualY = (centroid.y + member.actual.dy) * 100;
                  return (
                    <animated.g key={`deviation-${member.label}`} style={{ opacity: deviationSpring.opacity }}>
                      <defs>
                        <marker
                          id="arrowhead"
                          markerWidth="10"
                          markerHeight="7"
                          refX="9"
                          refY="3.5"
                          orient="auto"
                        >
                          <polygon points="0 0, 10 3.5, 0 7" fill="#ef4444" />
                        </marker>
                      </defs>
                      <line
                        x1={`${expectedX}%`}
                        y1={`${expectedY}%`}
                        x2={`${actualX}%`}
                        y2={`${actualY}%`}
                        stroke="#ef4444"
                        strokeWidth={2}
                        markerEnd="url(#arrowhead)"
                      />
                    </animated.g>
                  );
                })}

                {/* Centroid */}
                <animated.circle
                  cx={`${centroid.x * 100}%`}
                  cy={`${centroid.y * 100}%`}
                  r={10}
                  fill="#8b5cf6"
                  stroke="white"
                  strokeWidth={2}
                  style={{
                    opacity: centroidSpring.opacity,
                  }}
                />
              </svg>
            )}
          </div>
        </div>

        {/* Explanation panel */}
        <div className={styles.explanationPanel}>
          <div className={styles.panelTitle}>How It Works</div>

          <div className={styles.steps}>
            <div className={`${styles.step} ${showConstellation ? styles.stepActive : ""}`}>
              <div className={styles.stepNumber}>1</div>
              <div className={styles.stepContent}>
                <div className={styles.stepTitle}>Find Constellation</div>
                <div className={styles.stepDescription}>
                  Identify labels that frequently appear together: <strong>{labels.join(", ")}</strong>
                </div>
              </div>
            </div>

            <div className={`${styles.step} ${showConstellation ? styles.stepActive : ""}`}>
              <div className={styles.stepNumber}>2</div>
              <div className={styles.stepContent}>
                <div className={styles.stepTitle}>Calculate Centroid</div>
                <div className={styles.stepDescription}>
                  Find the center point of the group (purple dot)
                </div>
              </div>
            </div>

            <div className={`${styles.step} ${showExpected ? styles.stepActive : ""}`}>
              <div className={styles.stepNumber}>3</div>
              <div className={styles.stepContent}>
                <div className={styles.stepTitle}>Compare to Expected</div>
                <div className={styles.stepDescription}>
                  Each label should be at a learned offset from center (dashed circles)
                </div>
              </div>
            </div>

            <div className={`${styles.step} ${showDeviation ? styles.stepActive : ""}`}>
              <div className={styles.stepNumber}>4</div>
              <div className={styles.stepContent}>
                <div className={styles.stepTitle}>Flag Anomalies</div>
                <div className={styles.stepDescription}>
                  <strong>{data.constellation.flaggedLabel}</strong> is displaced from expected position
                </div>
              </div>
            </div>
          </div>

          {/* Legend */}
          <div className={styles.legend}>
            <div className={styles.legendItem}>
              <span className={styles.legendDot} style={{ background: "#8b5cf6" }} />
              <span>Centroid</span>
            </div>
            <div className={styles.legendItem}>
              <span className={styles.legendCircle} style={{ borderColor: "#10b981" }} />
              <span>Expected</span>
            </div>
            <div className={styles.legendItem}>
              <span className={styles.legendArrow} style={{ color: "#ef4444" }}>→</span>
              <span>Deviation</span>
            </div>
          </div>
        </div>
      </div>

      {/* Reasoning */}
      {showDeviation && (
        <animated.div className={styles.reasoning} style={{ opacity: deviationSpring.opacity }}>
          <div className={styles.reasoningTitle}>Detection Result</div>
          <div className={styles.reasoningText}>{data.constellation.reasoning}</div>
        </animated.div>
      )}
    </div>
  );
};

interface WordBoxProps {
  word: ConstellationWord;
  showHighlight: boolean;
}

const WordBox: React.FC<WordBoxProps> = ({ word, showHighlight }) => {
  const labelColor = word.label ? LABEL_COLORS[word.label] || "#666" : "#666";

  const bgColor = showHighlight
    ? `${labelColor}30`
    : word.label
    ? `${labelColor}15`
    : "transparent";
  const borderCol = showHighlight ? labelColor : word.label ? `${labelColor}50` : "transparent";

  return (
    <div
      className={`${styles.word} ${word.isFlagged ? styles.wordFlagged : ""}`}
      style={{
        left: `${word.x * 100}%`,
        top: `${word.y * 100}%`,
        backgroundColor: bgColor,
        borderColor: borderCol,
      }}
    >
      {word.text}
      {word.isInConstellation && (
        <span className={styles.wordLabel}>{word.label}</span>
      )}
    </div>
  );
};

export default ConstellationVisualization;
