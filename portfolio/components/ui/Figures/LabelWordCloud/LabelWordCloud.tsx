import { useEffect, useState, useMemo, useLayoutEffect, useRef, useCallback } from "react";
import { useSprings, animated, config } from "@react-spring/web";
import { useOptimizedInView } from "../../../../hooks/useOptimizedInView";
import { api } from "../../../../services/api";
import { LabelValidationCountResponse } from "../../../../types/api";
import { formatLabel } from "../../../../utils/formatLabel";
import {
  initializeAndSolve,
  optimizeForExternalLabels,
  MIN_FONT_INSIDE,
  MAX_FONT_INSIDE,
  EXTERNAL_FONT_SIZE,
  REFERENCE_FONT_SIZE,
  TEXT_PADDING_RATIO,
} from "./usePhysicsSimulation";
import type { LabelNode, LabelWordCloudProps } from "./types";
import styles from "./LabelWordCloud.module.css";

// The 21 CORE_LABELS from receipt_agent/constants.py
const CORE_LABELS = [
  "MERCHANT_NAME",
  "STORE_HOURS",
  "PHONE_NUMBER",
  "WEBSITE",
  "LOYALTY_ID",
  "ADDRESS_LINE",
  "DATE",
  "TIME",
  "PAYMENT_METHOD",
  "COUPON",
  "DISCOUNT",
  "PRODUCT_NAME",
  "QUANTITY",
  "UNIT_PRICE",
  "LINE_TOTAL",
  "SUBTOTAL",
  "TAX",
  "GRAND_TOTAL",
  "CHANGE",
  "CASH_BACK",
  "REFUND",
];

const CORE_LABELS_SET = new Set(CORE_LABELS);

/**
 * Format label name for display, split into lines for word cloud
 */
function formatLabelLines(label: string): string[] {
  return formatLabel(label).split(" ");
}

// Leader line settings
const MIN_LEADER_LENGTH = 20; // Shorter minimum keeps labels closer
const MAX_LEADER_LENGTH = 100;
const LENGTH_STEPS = 4;
const ANGLE_STEPS = 24;

/**
 * Check if a line segment intersects a circle
 */
function lineIntersectsCircle(
  x1: number, y1: number, x2: number, y2: number,
  cx: number, cy: number, radius: number
): boolean {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const fx = x1 - cx;
  const fy = y1 - cy;

  const a = dx * dx + dy * dy;
  const b = 2 * (fx * dx + fy * dy);
  const c = fx * fx + fy * fy - radius * radius;

  const discriminant = b * b - 4 * a * c;
  if (discriminant < 0) return false;

  const sqrtDisc = Math.sqrt(discriminant);
  const t1 = (-b - sqrtDisc) / (2 * a);
  const t2 = (-b + sqrtDisc) / (2 * a);

  return (t1 >= 0 && t1 <= 1) || (t2 >= 0 && t2 <= 1);
}

/**
 * Check if two rectangles overlap
 */
function rectsOverlap(
  a: { x1: number; y1: number; x2: number; y2: number },
  b: { x1: number; y1: number; x2: number; y2: number },
  padding: number = 4
): boolean {
  return !(
    a.x2 + padding < b.x1 ||
    b.x2 + padding < a.x1 ||
    a.y2 + padding < b.y1 ||
    b.y2 + padding < a.y1
  );
}

/**
 * Check if a point is inside a circle
 */
function pointInCircle(px: number, py: number, cx: number, cy: number, radius: number): boolean {
  const dx = px - cx;
  const dy = py - cy;
  return dx * dx + dy * dy < radius * radius;
}

/**
 * Get text bounds for a leader line position
 */
function getTextBounds(
  nodeX: number, nodeY: number, angle: number, length: number,
  text: string, fontSize: number
): { x1: number; y1: number; x2: number; y2: number } {
  const endX = nodeX + Math.cos(angle) * length;
  const endY = nodeY + Math.sin(angle) * length;
  const textWidth = text.length * fontSize * 0.55;
  const textHeight = fontSize * 1.4;
  const isRight = Math.cos(angle) > 0;

  return {
    x1: isRight ? endX + 4 : endX - 4 - textWidth,
    y1: endY - textHeight / 2,
    x2: isRight ? endX + 4 + textWidth : endX - 4,
    y2: endY + textHeight / 2,
  };
}

/**
 * Calculate leader lines for external labels.
 * Tracks placed labels to avoid text-to-text overlaps.
 */
function calculateLeaderLines(
  nodes: LabelNode[],
  centerX: number,
  centerY: number,
  width: number,
  height: number
): void {
  const externalNodes = nodes.filter((n) => n.textFitsInside === false);
  const placedLabels: Array<{ x1: number; y1: number; x2: number; y2: number }> = [];

  for (const node of externalNodes) {
    const dx = node.x - centerX;
    const dy = node.y - centerY;
    const defaultAngle = Math.atan2(dy, dx);
    const text = node.displayLines.join(" ");

    let bestAngle = defaultAngle;
    let bestLength = node.radius + MIN_LEADER_LENGTH;
    let bestScore = Infinity;

    // Search angles and lengths
    for (let ai = 0; ai < ANGLE_STEPS; ai++) {
      const step = Math.PI / 18; // 10 degrees
      const angleOffset = (Math.floor((ai + 1) / 2) * step) * (ai % 2 === 0 ? 1 : -1);
      const testAngle = defaultAngle + angleOffset;

      for (let li = 0; li < LENGTH_STEPS; li++) {
        const length = node.radius + MIN_LEADER_LENGTH +
          (li / (LENGTH_STEPS - 1)) * (MAX_LEADER_LENGTH - MIN_LEADER_LENGTH);

        const textBounds = getTextBounds(node.x, node.y, testAngle, length, text, node.fontSize);

        // Skip if out of bounds
        if (textBounds.x1 < 5 || textBounds.x2 > width - 5 ||
            textBounds.y1 < 5 || textBounds.y2 > height - 5) {
          continue;
        }

        let score = 0;

        // Check leader line crossing circles (highest priority)
        const lineStartX = node.x + Math.cos(testAngle) * node.radius;
        const lineStartY = node.y + Math.sin(testAngle) * node.radius;
        const lineEndX = node.x + Math.cos(testAngle) * length;
        const lineEndY = node.y + Math.sin(testAngle) * length;

        for (const other of nodes) {
          if (other.label === node.label) continue;
          if (lineIntersectsCircle(lineStartX, lineStartY, lineEndX, lineEndY,
                                    other.x, other.y, other.radius + 3)) {
            score += 1000; // Heavy penalty
          }
        }

        // Check text overlapping circles (check multiple points, not just center)
        const textCenterX = (textBounds.x1 + textBounds.x2) / 2;
        const textCenterY = (textBounds.y1 + textBounds.y2) / 2;
        for (const other of nodes) {
          if (other.label === node.label) continue;
          // Check center and all four corners
          if (pointInCircle(textCenterX, textCenterY, other.x, other.y, other.radius + 5) ||
              pointInCircle(textBounds.x1, textBounds.y1, other.x, other.y, other.radius) ||
              pointInCircle(textBounds.x2, textBounds.y1, other.x, other.y, other.radius) ||
              pointInCircle(textBounds.x1, textBounds.y2, other.x, other.y, other.radius) ||
              pointInCircle(textBounds.x2, textBounds.y2, other.x, other.y, other.radius)) {
            score += 500;
          }
        }

        // Check text overlapping already-placed external labels
        for (const placed of placedLabels) {
          if (rectsOverlap(textBounds, placed, 6)) {
            score += 500;
          }
        }

        // Prefer shorter lengths and angles close to default
        score += length * 0.5;
        score += Math.abs(angleOffset) * 10;

        if (score < bestScore) {
          bestScore = score;
          bestAngle = testAngle;
          bestLength = length;

          // Perfect position - stop searching
          if (score < 50) break;
        }
      }

      if (bestScore < 50) break;
    }

    node.leaderAngle = bestAngle;
    node.leaderLength = bestLength;

    // Track this label's text bounds for subsequent labels
    placedLabels.push(getTextBounds(node.x, node.y, bestAngle, bestLength, text, node.fontSize));
  }
}

/**
 * Get text anchor based on angle
 */
function getTextAnchor(angle: number): "start" | "end" | "middle" {
  const cos = Math.cos(angle);
  if (Math.abs(cos) < 0.3) return "middle";
  return cos > 0 ? "start" : "end";
}

const LabelWordCloud: React.FC<LabelWordCloudProps> = ({
  width: propWidth = 700,
  height: propHeight = 500,
}) => {
  const [ref, inView] = useOptimizedInView({
    threshold: 0.3,
    triggerOnce: true,
  });

  // Responsive sizing - use more square layout on mobile
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };
    checkMobile();
    window.addEventListener("resize", checkMobile);
    return () => window.removeEventListener("resize", checkMobile);
  }, []);

  // On mobile: use square-ish layout for better readability
  // On desktop: use the provided dimensions
  const width = isMobile ? 500 : propWidth;
  const height = isMobile ? 550 : propHeight;

  const [data, setData] = useState<LabelValidationCountResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [measuredNodes, setMeasuredNodes] = useState<LabelNode[] | null>(null);

  // Refs for measuring text
  const measureSvgRef = useRef<SVGSVGElement>(null);
  const textRefs = useRef<Map<string, SVGTextElement>>(new Map());

  const centerX = width / 2;
  const centerY = height / 2;

  // Fetch label validation counts
  useEffect(() => {
    api
      .fetchLabelValidationCount()
      .then(setData)
      .catch((err) => {
        console.error("Failed to fetch label validation counts:", err);
        setError("Failed to load data");
      });
  }, []);

  // Compute initial nodes (without textFitsInside determined yet)
  const { startNodes, endNodes } = useMemo(() => {
    if (!data) return { startNodes: [], endNodes: [] };

    const labelData = Object.entries(data)
      .filter(([label]) => CORE_LABELS_SET.has(label))
      .map(([label, counts]) => ({
        label,
        displayLines: formatLabelLines(label),
        validCount: counts.VALID || 0,
      }));

    return initializeAndSolve(labelData, width, height);
  }, [data, width, height]);

  // Callback to store text refs
  const setTextRef = useCallback((label: string, el: SVGTextElement | null) => {
    if (el) {
      textRefs.current.set(label, el);
    } else {
      textRefs.current.delete(label);
    }
  }, []);

  // Measure text and calculate max font size using useLayoutEffect (runs before paint)
  useLayoutEffect(() => {
    if (endNodes.length === 0) return;

    // Phase 1: Measure each text element and determine which need external labels
    const externalLabels = new Set<string>();
    const measurementResults = new Map<string, { textFitsInside: boolean; fontSize: number }>();

    for (const node of endNodes) {
      const textEl = textRefs.current.get(node.label);
      if (!textEl) {
        externalLabels.add(node.label);
        measurementResults.set(node.label, { textFitsInside: false, fontSize: EXTERNAL_FONT_SIZE });
        continue;
      }

      try {
        const bbox = textEl.getBBox();
        const inscribedSize = node.radius * Math.SQRT2 * TEXT_PADDING_RATIO;
        const maxFontByWidth = (inscribedSize / bbox.width) * REFERENCE_FONT_SIZE;
        const maxFontByHeight = (inscribedSize / bbox.height) * REFERENCE_FONT_SIZE;
        const maxFittingFont = Math.min(maxFontByWidth, maxFontByHeight);

        const textFitsInside = maxFittingFont >= MIN_FONT_INSIDE;
        const fontSize = textFitsInside
          ? Math.min(maxFittingFont, MAX_FONT_INSIDE)
          : EXTERNAL_FONT_SIZE;

        if (!textFitsInside) {
          externalLabels.add(node.label);
        }
        measurementResults.set(node.label, { textFitsInside, fontSize });
      } catch {
        externalLabels.add(node.label);
        measurementResults.set(node.label, { textFitsInside: false, fontSize: EXTERNAL_FONT_SIZE });
      }
    }

    // Phase 2: Re-run physics to push external-label circles to the edges
    const optimizedNodes = optimizeForExternalLabels(endNodes, externalLabels, width, height);

    // Phase 3: Apply measurement results to optimized positions
    const measured = optimizedNodes.map((node) => {
      const result = measurementResults.get(node.label)!;
      return { ...node, ...result };
    });

    // Phase 4: Calculate simple leader lines (now pointing outward works)
    calculateLeaderLines(measured, centerX, centerY, width, height);

    setMeasuredNodes(measured);
  }, [endNodes, centerX, centerY, width, height]);

  // Spring animation
  const springs = useSprings(
    measuredNodes?.length ?? 0,
    (measuredNodes ?? []).map((endNode, i) => {
      const startNode = startNodes[i];
      return {
        from: { x: startNode?.x ?? 0, y: startNode?.y ?? 0, opacity: 0 },
        to: inView
          ? { x: endNode.x, y: endNode.y, opacity: 1 }
          : { x: startNode?.x ?? 0, y: startNode?.y ?? 0, opacity: 0 },
        config: {
          ...config.wobbly,
          mass: 1 + endNode.radius / 50,
          tension: 120,
          friction: 14,
        },
        delay: inView ? i * 30 : 0,
      };
    })
  );

  if (error) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.error}>{error}</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loading}>Loading...</div>
      </div>
    );
  }

  // Phase 1: Render hidden SVG to measure text at reference font size
  if (!measuredNodes && endNodes.length > 0) {
    const refLineHeight = REFERENCE_FONT_SIZE * 1.2;

    return (
      <div ref={ref} className={styles.container}>
        <svg
          ref={measureSvgRef}
          viewBox={`0 0 ${width} ${height}`}
          className={styles.svg}
          style={{ visibility: "hidden", position: "absolute" }}
          aria-hidden="true"
        >
          {endNodes.map((node) => {
            const totalHeight = node.displayLines.length * refLineHeight;
            const startY = -totalHeight / 2 + refLineHeight / 2;

            return (
              <text
                key={node.label}
                ref={(el) => setTextRef(node.label, el)}
                x={node.x}
                y={node.y}
                fontSize={REFERENCE_FONT_SIZE}
                textAnchor="middle"
              >
                {node.displayLines.map((line, lineIndex) => (
                  <tspan
                    key={lineIndex}
                    x={node.x}
                    y={node.y + startY + lineIndex * refLineHeight}
                    dominantBaseline="middle"
                  >
                    {line}
                  </tspan>
                ))}
              </text>
            );
          })}
        </svg>
      </div>
    );
  }

  if (!measuredNodes || measuredNodes.length === 0) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loading}>Loading...</div>
      </div>
    );
  }

  // Phase 2: Render actual visualization with measured data
  return (
    <div ref={ref} className={styles.container}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className={styles.svg}
        aria-label="Word cloud showing CORE_LABELS sized by validation count"
      >
        {/* First pass: render all circles */}
        {springs.map((spring, index) => {
          const node = measuredNodes[index];
          if (!node) return null;

          return (
            <animated.g
              key={`circle-${node.label}`}
              style={{
                opacity: spring.opacity,
                transform: spring.x.to(
                  (x) => `translate(${x}px, ${spring.y.get()}px)`
                ),
              }}
            >
              <circle r={node.radius} className={styles.labelCircle} />
            </animated.g>
          );
        })}

        {/* Second pass: render text inside large circles */}
        {springs.map((spring, index) => {
          const node = measuredNodes[index];
          if (!node || !node.textFitsInside) return null;

          const lineHeight = node.fontSize * 1.2;
          const totalHeight = node.displayLines.length * lineHeight;
          const startY = -totalHeight / 2 + lineHeight / 2;

          return (
            <animated.g
              key={`text-${node.label}`}
              style={{
                opacity: spring.opacity,
                transform: spring.x.to(
                  (x) => `translate(${x}px, ${spring.y.get()}px)`
                ),
              }}
            >
              <text
                style={{ fontSize: `${node.fontSize}px` }}
                className={styles.labelText}
                textAnchor="middle"
              >
                {node.displayLines.map((line, lineIndex) => (
                  <tspan
                    key={lineIndex}
                    x={0}
                    y={startY + lineIndex * lineHeight}
                    dominantBaseline="middle"
                  >
                    {line}
                  </tspan>
                ))}
              </text>
            </animated.g>
          );
        })}

        {/* Third pass: render leader lines and external text */}
        {springs.map((spring, index) => {
          const node = measuredNodes[index];
          if (!node || node.textFitsInside !== false) return null;

          const angle = node.leaderAngle ?? Math.atan2(node.y - centerY, node.x - centerX);
          const length = node.leaderLength ?? node.radius + 35;
          const textAnchor = getTextAnchor(angle);

          const endX = Math.cos(angle) * length;
          const endY = Math.sin(angle) * length;

          return (
            <animated.g
              key={`leader-${node.label}`}
              style={{
                opacity: spring.opacity,
                transform: spring.x.to(
                  (x) => `translate(${x}px, ${spring.y.get()}px)`
                ),
              }}
            >
              <line
                x1={Math.cos(angle) * node.radius}
                y1={Math.sin(angle) * node.radius}
                x2={endX}
                y2={endY}
                className={styles.leaderLine}
              />
              <text
                x={endX + (textAnchor === "start" ? 4 : textAnchor === "end" ? -4 : 0)}
                y={endY}
                style={{ fontSize: `${node.fontSize}px` }}
                className={styles.externalText}
                textAnchor={textAnchor}
                dominantBaseline="middle"
              >
                {node.displayLines.join(" ")}
              </text>
            </animated.g>
          );
        })}
      </svg>
    </div>
  );
};

export default LabelWordCloud;
