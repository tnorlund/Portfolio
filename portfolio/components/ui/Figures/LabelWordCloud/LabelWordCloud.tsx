import { useEffect, useState, useRef } from "react";
import { useSprings, animated } from "@react-spring/web";
import { useOptimizedInView } from "../../../../hooks/useOptimizedInView";
import { api } from "../../../../services/api";
import { LabelValidationCountResponse } from "../../../../types/api";
import {
  usePhysicsSimulation,
  initializeNodes,
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

const MIN_FONT_SIZE = 14;
const MAX_FONT_SIZE = 42;

/**
 * Format label name for display: lowercase with spaces
 */
function formatLabelName(label: string): string {
  return label.toLowerCase().replace(/_/g, " ");
}

const LabelWordCloud: React.FC<LabelWordCloudProps> = ({
  width = 700,
  height = 400,
}) => {
  const [ref, inView] = useOptimizedInView({
    threshold: 0.3,
    triggerOnce: true,
  });

  const [data, setData] = useState<LabelValidationCountResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<LabelNode[]>([]);
  const [isSimulating, setIsSimulating] = useState(false);
  const animationFrameRef = useRef<number | null>(null);
  const nodesRef = useRef<LabelNode[]>([]);
  const hasStartedRef = useRef(false);

  const { simulateStep } = usePhysicsSimulation(width, height);

  // Keep nodesRef in sync with nodes state
  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

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

  // Initialize nodes when data is loaded
  useEffect(() => {
    if (!data) return;

    const labelData = Object.entries(data)
      .filter(([label]) => CORE_LABELS_SET.has(label))
      .map(([label, counts]) => ({
        label,
        displayName: formatLabelName(label),
        validCount: counts.VALID || 0,
      }));

    const initialNodes = initializeNodes(
      labelData,
      width,
      height,
      MIN_FONT_SIZE,
      MAX_FONT_SIZE
    );

    setNodes(initialNodes);
  }, [data, width, height]);

  // Start simulation once when component comes into view
  useEffect(() => {
    if (inView && nodes.length > 0 && !hasStartedRef.current) {
      hasStartedRef.current = true;
      setIsSimulating(true);
    }
  }, [inView, nodes.length]);

  // Run physics simulation
  useEffect(() => {
    if (!isSimulating || nodesRef.current.length === 0) return;

    const animate = () => {
      // Create a mutable copy for simulation
      const nodesCopy = nodesRef.current.map((n) => ({ ...n }));
      const shouldContinue = simulateStep(nodesCopy);

      setNodes(nodesCopy);

      if (shouldContinue) {
        animationFrameRef.current = requestAnimationFrame(animate);
      } else {
        setIsSimulating(false);
      }
    };

    animationFrameRef.current = requestAnimationFrame(animate);

    return () => {
      if (animationFrameRef.current !== null) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [isSimulating, simulateStep]);

  // Spring animations for smooth position updates
  const springs = useSprings(
    nodes.length,
    nodes.map((node) => ({
      x: node.x,
      y: node.y,
      opacity: 1,
      config: { tension: 120, friction: 20 },
    }))
  );

  if (error) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.error}>{error}</div>
      </div>
    );
  }

  if (!data || nodes.length === 0) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loading}>Loading...</div>
      </div>
    );
  }

  return (
    <div ref={ref} className={styles.container}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className={styles.svg}
        aria-label="Word cloud showing CORE_LABELS sized by validation count"
      >
        {springs.map((spring, index) => {
          const node = nodes[index];
          if (!node) return null;

          return (
            <animated.text
              key={node.label}
              x={spring.x}
              y={spring.y}
              fontSize={node.fontSize}
              className={styles.label}
              style={{ opacity: spring.opacity }}
            >
              {node.displayName}
            </animated.text>
          );
        })}
      </svg>
    </div>
  );
};

export default LabelWordCloud;
