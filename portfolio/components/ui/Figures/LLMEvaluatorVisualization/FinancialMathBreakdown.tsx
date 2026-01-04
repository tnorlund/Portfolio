import React from "react";
import { animated, useSpring } from "@react-spring/web";
import { FinancialMathResult, DECISION_COLORS } from "./mockData";
import styles from "./LLMEvaluatorVisualization.module.css";

interface FinancialMathBreakdownProps {
  result: FinancialMathResult;
  showResult: boolean;
  animationDelay: number;
}

const FinancialMathBreakdown: React.FC<FinancialMathBreakdownProps> = ({
  result,
  showResult,
  animationDelay,
}) => {
  const containerSpring = useSpring({
    from: { opacity: 0, transform: "translateY(20px)" },
    to: { opacity: 1, transform: "translateY(0)" },
    delay: animationDelay,
    config: { tension: 200, friction: 20 },
  });

  const subtotalSpring = useSpring({
    from: { opacity: 0, scale: 0.8 },
    to: { opacity: showResult ? 1 : 0, scale: showResult ? 1 : 0.8 },
    delay: animationDelay + 200,
    config: { tension: 200, friction: 20 },
  });

  const taxSpring = useSpring({
    from: { opacity: 0, scale: 0.8 },
    to: { opacity: showResult ? 1 : 0, scale: showResult ? 1 : 0.8 },
    delay: animationDelay + 400,
    config: { tension: 200, friction: 20 },
  });

  const totalSpring = useSpring({
    from: { opacity: 0, scale: 0.8 },
    to: { opacity: showResult ? 1 : 0, scale: showResult ? 1 : 0.8 },
    delay: animationDelay + 600,
    config: { tension: 200, friction: 20 },
  });

  const resultSpring = useSpring({
    from: { opacity: 0 },
    to: { opacity: showResult ? 1 : 0 },
    delay: animationDelay + 800,
    config: { tension: 200, friction: 20 },
  });

  const isValid = result.decision === "VALID";
  const wrongValueClass = (value: "SUBTOTAL" | "TAX" | "GRAND_TOTAL") =>
    result.wrongValue === value ? styles.mathAmountInvalid : styles.mathAmountValid;

  return (
    <animated.div className={styles.mathContainer} style={containerSpring}>
      <h4 className={styles.mathTitle}>
        <span>🧮</span>
        Financial Math Validation
      </h4>

      <div className={styles.mathEquation}>
        {/* SUBTOTAL */}
        <animated.div className={styles.mathValue} style={subtotalSpring}>
          <span className={styles.mathLabel}>Subtotal</span>
          <span className={`${styles.mathAmount} ${wrongValueClass("SUBTOTAL")}`}>
            ${result.subtotal.toFixed(2)}
          </span>
        </animated.div>

        <animated.span className={styles.mathOperator} style={subtotalSpring}>
          +
        </animated.span>

        {/* TAX */}
        <animated.div className={styles.mathValue} style={taxSpring}>
          <span className={styles.mathLabel}>Tax</span>
          <span className={`${styles.mathAmount} ${wrongValueClass("TAX")}`}>
            ${result.tax.toFixed(2)}
          </span>
        </animated.div>

        <animated.span className={styles.mathOperator} style={taxSpring}>
          =
        </animated.span>

        {/* Expected Total */}
        <animated.div className={styles.mathValue} style={totalSpring}>
          <span className={styles.mathLabel}>Expected</span>
          <span className={`${styles.mathAmount} ${styles.mathAmountValid}`}>
            ${result.expectedTotal.toFixed(2)}
          </span>
        </animated.div>

        {/* Show comparison if there's a mismatch */}
        {!isValid && (
          <>
            <animated.span className={styles.mathOperator} style={totalSpring}>
              ≠
            </animated.span>

            <animated.div className={styles.mathValue} style={totalSpring}>
              <span className={styles.mathLabel}>Actual</span>
              <span className={`${styles.mathAmount} ${styles.mathAmountInvalid}`}>
                ${result.actualTotal.toFixed(2)}
              </span>
            </animated.div>
          </>
        )}
      </div>

      {/* Result with reasoning */}
      <animated.div className={styles.mathResult} style={resultSpring}>
        <span
          className={styles.mathResultIcon}
          style={{ color: DECISION_COLORS[result.decision] }}
        >
          {isValid ? "✓" : "✗"}
        </span>
        <span className={styles.mathResultText}>{result.reasoning}</span>
      </animated.div>
    </animated.div>
  );
};

export default FinancialMathBreakdown;
