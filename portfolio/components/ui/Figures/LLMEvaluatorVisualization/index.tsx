import React, { useState, useEffect, useRef } from "react";
import { useInView } from "react-intersection-observer";
import PipelineFlow from "./PipelineFlow";
import SubagentCards from "./SubagentCards";
import FinancialMathBreakdown from "./FinancialMathBreakdown";
import mockDataWithError from "./mockData";
import styles from "./LLMEvaluatorVisualization.module.css";

const LLMEvaluatorVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: true,
  });

  const [currentStageIndex, setCurrentStageIndex] = useState(0);
  const [showSubagentResults, setShowSubagentResults] = useState(false);
  const [showFinancialResult, setShowFinancialResult] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const hasStartedAnimation = useRef(false);

  // Use the error case data for a more interesting demo
  const data = mockDataWithError;

  // Animation sequence when component comes into view
  useEffect(() => {
    if (!inView || hasStartedAnimation.current) return;

    hasStartedAnimation.current = true;

    // Stage 0: Input (already shown)
    setCurrentStageIndex(0);

    // Stage 1: Currency & Metadata (parallel)
    const stage1Timeout = setTimeout(() => {
      setCurrentStageIndex(1);
      setIsProcessing(true);
    }, 500);

    // Show subagent results
    const resultsTimeout = setTimeout(() => {
      setShowSubagentResults(true);
      setIsProcessing(false);
      setCurrentStageIndex(2);
    }, 2000);

    // Stage 2: Complete metadata
    const stage2Timeout = setTimeout(() => {
      setCurrentStageIndex(3);
    }, 2500);

    // Stage 3: Financial math
    const stage3Timeout = setTimeout(() => {
      setCurrentStageIndex(3);
      setShowFinancialResult(true);
    }, 3000);

    // Stage 4: Output
    const stage4Timeout = setTimeout(() => {
      setCurrentStageIndex(4);
    }, 4000);

    return () => {
      clearTimeout(stage1Timeout);
      clearTimeout(resultsTimeout);
      clearTimeout(stage2Timeout);
      clearTimeout(stage3Timeout);
      clearTimeout(stage4Timeout);
    };
  }, [inView]);

  // Calculate summary statistics
  const allEvaluations = [
    ...data.evaluations.currency,
    ...data.evaluations.metadata,
  ];
  const validCount = allEvaluations.filter((e) => e.decision === "VALID").length;
  const invalidCount = allEvaluations.filter((e) => e.decision === "INVALID").length;
  const reviewCount = allEvaluations.filter(
    (e) => e.decision === "NEEDS_REVIEW"
  ).length;

  return (
    <div ref={ref} className={styles.container}>
      {/* Pipeline Flow */}
      <PipelineFlow
        stages={data.pipeline}
        currentStageIndex={currentStageIndex}
      />

      {/* Subagent Cards */}
      <SubagentCards
        currencyEvaluations={data.evaluations.currency}
        metadataEvaluations={data.evaluations.metadata}
        isProcessing={isProcessing}
        showResults={showSubagentResults}
      />

      {/* Financial Math Breakdown */}
      <FinancialMathBreakdown
        result={data.evaluations.financial}
        showResult={showFinancialResult}
        animationDelay={0}
      />

      {/* Summary Statistics */}
      {currentStageIndex >= 4 && (
        <div className={styles.summaryContainer}>
          <div className={styles.summaryStat}>
            <span className={`${styles.summaryValue} ${styles.summaryValid}`}>
              {validCount}
            </span>
            <span className={styles.summaryLabel}>Valid</span>
          </div>
          <div className={styles.summaryStat}>
            <span className={`${styles.summaryValue} ${styles.summaryInvalid}`}>
              {invalidCount}
            </span>
            <span className={styles.summaryLabel}>Invalid</span>
          </div>
          {reviewCount > 0 && (
            <div className={styles.summaryStat}>
              <span className={`${styles.summaryValue} ${styles.summaryReview}`}>
                {reviewCount}
              </span>
              <span className={styles.summaryLabel}>Needs Review</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default LLMEvaluatorVisualization;
