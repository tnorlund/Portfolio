import React, { useState, useEffect, useRef, useMemo } from "react";
import { useInView } from "react-intersection-observer";
import { animated, useSpring } from "@react-spring/web";
import { ConstellationData, ConstellationWord } from "./types";
import { api } from "../../../../services/api";
import { GeometricAnomalyCacheResponse, ReceiptImageData } from "../../../../types/api";
import { detectImageFormatSupport, getBestImageUrl, FormatSupport } from "../../../../utils/imageFormat";
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
  const [imageData, setImageData] = useState<ReceiptImageData | null>(null);
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showConstellation, setShowConstellation] = useState(false);
  const [showExpected, setShowExpected] = useState(false);
  const [showDeviation, setShowDeviation] = useState(false);
  const hasStartedAnimation = useRef(false);

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Fetch real data on mount
  useEffect(() => {
    api
      .fetchGeometricAnomaly()
      .then((response) => {
        const transformed = transformApiResponse(response);
        if (transformed) {
          setData(transformed);
          setImageData(response.receipt.image || null);
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

  // Get the best image URL
  const imageUrl = useMemo(() => {
    if (!formatSupport || !imageData?.cdn_s3_key) return null;
    return getBestImageUrl(imageData as Parameters<typeof getBestImageUrl>[0], formatSupport, "medium");
  }, [formatSupport, imageData]);

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
  const hasImage = imageUrl && imageData;

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
            {hasImage ? (
              // Actual receipt image with SVG overlay
              <div className={styles.receiptImageWrapper}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={imageUrl}
                  alt="Receipt"
                  className={styles.receiptImage}
                />
                {/* SVG overlay for bounding boxes and constellation */}
                <svg
                  className={styles.svgOverlay}
                  viewBox={`0 0 ${imageData.width} ${imageData.height}`}
                  preserveAspectRatio="xMidYMid meet"
                >
                  {/* Bounding boxes for constellation words */}
                  {showConstellation && data.receipt.words
                    .filter(w => w.isInConstellation)
                    .map((word) => {
                      const labelColor = word.label ? LABEL_COLORS[word.label] || "#666" : "#666";
                      // Convert normalized coords to pixel coords
                      // Note: y is from bottom in normalized coords, so we need to invert
                      const x = word.x * imageData.width;
                      const y = (1 - word.y - word.height) * imageData.height;
                      const width = word.width * imageData.width;
                      const height = word.height * imageData.height;

                      return (
                        <g key={word.id}>
                          <rect
                            x={x}
                            y={y}
                            width={width}
                            height={height}
                            fill={word.isFlagged ? "#ef444440" : `${labelColor}30`}
                            stroke={word.isFlagged ? "#ef4444" : labelColor}
                            strokeWidth={word.isFlagged ? 3 : 2}
                          />
                          {/* Label text */}
                          <text
                            x={x + width / 2}
                            y={y - 5}
                            textAnchor="middle"
                            fontSize={Math.min(14, height * 0.8)}
                            fill={word.isFlagged ? "#ef4444" : labelColor}
                            fontWeight="bold"
                          >
                            {word.label}
                          </text>
                        </g>
                      );
                    })}

                  {/* Constellation lines from centroid to members */}
                  {showConstellation && members.map((member) => {
                    // Convert normalized centroid + offset to pixel coords
                    const centroidPx = {
                      x: centroid.x * imageData.width,
                      y: (1 - centroid.y) * imageData.height,
                    };
                    const actualPx = {
                      x: (centroid.x + member.actual.dx) * imageData.width,
                      y: (1 - centroid.y - member.actual.dy) * imageData.height,
                    };

                    return (
                      <animated.line
                        key={`line-${member.label}`}
                        x1={centroidPx.x}
                        y1={centroidPx.y}
                        x2={actualPx.x}
                        y2={actualPx.y}
                        stroke="#8b5cf6"
                        strokeWidth={2}
                        strokeDasharray="8,4"
                        style={{ opacity: centroidSpring.opacity }}
                      />
                    );
                  })}

                  {/* Expected position for flagged member */}
                  {showExpected && members.filter(m => m.isFlagged).map((member) => {
                    const expectedPx = {
                      x: (centroid.x + member.expected.dx) * imageData.width,
                      y: (1 - centroid.y - member.expected.dy) * imageData.height,
                    };

                    return (
                      <animated.g key={`expected-${member.label}`} style={{ opacity: expectedSpring.opacity }}>
                        <circle
                          cx={expectedPx.x}
                          cy={expectedPx.y}
                          r={20}
                          fill="none"
                          stroke="#10b981"
                          strokeWidth={3}
                          strokeDasharray="8,4"
                        />
                        <text
                          x={expectedPx.x}
                          y={expectedPx.y - 28}
                          textAnchor="middle"
                          fontSize="14"
                          fill="#10b981"
                          fontWeight="bold"
                        >
                          expected
                        </text>
                      </animated.g>
                    );
                  })}

                  {/* Deviation arrow */}
                  {showDeviation && members.filter(m => m.isFlagged).map((member) => {
                    const expectedPx = {
                      x: (centroid.x + member.expected.dx) * imageData.width,
                      y: (1 - centroid.y - member.expected.dy) * imageData.height,
                    };
                    const actualPx = {
                      x: (centroid.x + member.actual.dx) * imageData.width,
                      y: (1 - centroid.y - member.actual.dy) * imageData.height,
                    };

                    return (
                      <animated.g key={`deviation-${member.label}`} style={{ opacity: deviationSpring.opacity }}>
                        <defs>
                          <marker
                            id="arrowhead-img"
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
                          x1={expectedPx.x}
                          y1={expectedPx.y}
                          x2={actualPx.x}
                          y2={actualPx.y}
                          stroke="#ef4444"
                          strokeWidth={3}
                          markerEnd="url(#arrowhead-img)"
                        />
                      </animated.g>
                    );
                  })}

                  {/* Centroid marker */}
                  {showConstellation && (
                    <animated.circle
                      cx={centroid.x * imageData.width}
                      cy={(1 - centroid.y) * imageData.height}
                      r={15}
                      fill="#8b5cf6"
                      stroke="white"
                      strokeWidth={3}
                      style={{ opacity: centroidSpring.opacity }}
                    />
                  )}
                </svg>
              </div>
            ) : (
              // Fallback: text-based word boxes (no image available)
              <>
                {data.receipt.words.map((word) => (
                  <WordBox
                    key={word.id}
                    word={word}
                    showHighlight={showConstellation && word.isInConstellation}
                  />
                ))}
                {/* Constellation overlay for fallback */}
                {showConstellation && (
                  <svg className={styles.constellationOverlay}>
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
                    <animated.circle
                      cx={`${centroid.x * 100}%`}
                      cy={`${centroid.y * 100}%`}
                      r={10}
                      fill="#8b5cf6"
                      stroke="white"
                      strokeWidth={2}
                      style={{ opacity: centroidSpring.opacity }}
                    />
                  </svg>
                )}
              </>
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
