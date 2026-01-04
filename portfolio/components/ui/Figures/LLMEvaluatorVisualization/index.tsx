import React, { useState, useEffect, useRef } from "react";
import { useInView } from "react-intersection-observer";
import PipelineFlow from "./PipelineFlow";
import SubagentCards from "./SubagentCards";
import FinancialMathBreakdown from "./FinancialMathBreakdown";
import mockDataWithError, { LLMEvaluatorData, EvaluationResult, FinancialMathResult, PipelineStage } from "./mockData";
import { api } from "../../../../services/api";
import { LLMEvaluatorCacheResponse, LLMEvaluation, LLMFinancialResult } from "../../../../types/api";
import styles from "./LLMEvaluatorVisualization.module.css";

/**
 * Transform API response to the component's internal data format
 */
function transformApiResponse(response: LLMEvaluatorCacheResponse): LLMEvaluatorData {
  // Transform currency evaluations
  const currencyEvals: EvaluationResult[] = response.evaluations.currency.map((e: LLMEvaluation) => ({
    wordText: e.word_text,
    currentLabel: e.current_label,
    decision: e.decision,
    reasoning: e.reasoning,
    suggestedLabel: e.suggested_label,
    confidence: e.confidence,
  }));

  // Transform metadata evaluations
  const metadataEvals: EvaluationResult[] = response.evaluations.metadata.map((e: LLMEvaluation) => ({
    wordText: e.word_text,
    currentLabel: e.current_label,
    decision: e.decision,
    reasoning: e.reasoning,
    suggestedLabel: e.suggested_label,
    confidence: e.confidence,
  }));

  // Transform financial result
  const financialResult: FinancialMathResult = {
    equation: response.evaluations.financial.equation,
    subtotal: response.evaluations.financial.subtotal,
    tax: response.evaluations.financial.tax,
    expectedTotal: response.evaluations.financial.expected_total,
    actualTotal: response.evaluations.financial.actual_total,
    difference: response.evaluations.financial.difference,
    decision: response.evaluations.financial.decision,
    reasoning: response.evaluations.financial.reasoning,
    wrongValue: response.evaluations.financial.wrong_value,
  };

  // Transform pipeline stages
  const pipeline: PipelineStage[] = response.pipeline.map((p) => ({
    id: p.id,
    name: p.name,
    status: p.status,
  }));

  return {
    receipt: {
      merchantName: response.receipt.merchant_name,
      lineItems: response.receipt.line_items,
      subtotal: response.receipt.subtotal,
      tax: response.receipt.tax,
      grandTotal: response.receipt.grand_total,
    },
    evaluations: {
      currency: currencyEvals,
      metadata: metadataEvals,
      financial: financialResult,
    },
    pipeline,
  };
}

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
  const [data, setData] = useState<LLMEvaluatorData>(mockDataWithError);
  const [isLoading, setIsLoading] = useState(true);
  const [usingRealData, setUsingRealData] = useState(false);

  // Fetch real data on mount
  useEffect(() => {
    api
      .fetchLLMEvaluation()
      .then((response) => {
        const transformed = transformApiResponse(response);
        // Only use API data if it has evaluations
        if (transformed.evaluations.currency.length > 0 || transformed.evaluations.metadata.length > 0) {
          setData(transformed);
          setUsingRealData(true);
        }
      })
      .catch((error) => {
        console.warn("Failed to fetch LLM evaluation data, using mock data:", error);
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, []);

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

  // Show loading state if still loading
  if (isLoading) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loadingState}>Loading visualization...</div>
      </div>
    );
  }

  return (
    <div ref={ref} className={styles.container}>
      {/* Data source indicator */}
      {usingRealData && (
        <div className={styles.dataSourceBadge}>Live Data</div>
      )}
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
