import React, { useState, useEffect, useRef } from "react";
import { useInView } from "react-intersection-observer";
import ReceiptView from "./ReceiptView";
import PatternScatterPlot from "./PatternScatterPlot";
import { mockData, GeometricAnomalyData } from "./mockData";
import { api } from "../../../../services/api";
import { GeometricAnomalyCacheResponse } from "../../../../types/api";
import styles from "./GeometricAnomalyVisualization.module.css";

type AnimationStage = "idle" | "labeling" | "detecting" | "pattern" | "complete";

const STAGE_LABELS: Record<AnimationStage, string> = {
  idle: "Waiting",
  labeling: "Labeling Words",
  detecting: "Detecting Anomalies",
  pattern: "Analyzing Patterns",
  complete: "Complete",
};

/**
 * Transform API response to the component's internal data format
 */
function transformApiResponse(response: GeometricAnomalyCacheResponse): GeometricAnomalyData {
  return {
    receipt: {
      imageId: response.receipt.image_id,
      receiptId: response.receipt.receipt_id,
      merchantName: response.receipt.merchant_name,
      words: response.receipt.words.map((w) => ({
        id: `${w.line_id}-${w.word_id}`,
        text: w.text,
        x: w.x,
        y: w.y,
        width: w.width,
        height: w.height,
        label: w.label,
        isFlagged: w.is_flagged,
        anomalyType: w.anomaly_type,
        reasoning: w.reasoning,
      })),
    },
    patterns: {
      labelPairs: response.patterns.label_pairs.map((p) => ({
        from: p.from_label,
        to: p.to_label,
        observations: p.observations,
        mean: p.mean,
        stdDeviation: p.std_deviation,
      })),
    },
    flaggedWord: response.flagged_word
      ? {
          wordId: `${response.flagged_word.line_id}-${response.flagged_word.word_id}`,
          referenceLabel: response.flagged_word.reference_label,
          expected: response.flagged_word.expected,
          actual: response.flagged_word.actual,
          zScore: response.flagged_word.z_score,
          threshold: response.flagged_word.threshold,
        }
      : null,
  };
}

const GeometricAnomalyVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: true,
  });

  const [stage, setStage] = useState<AnimationStage>("idle");
  const [animationProgress, setAnimationProgress] = useState(0);
  const [selectedPatternIndex, setSelectedPatternIndex] = useState(0);
  const [highlightedWordId, setHighlightedWordId] = useState<string | null>(null);
  const hasStartedAnimation = useRef(false);
  const [data, setData] = useState<GeometricAnomalyData>(mockData);
  const [isLoading, setIsLoading] = useState(true);
  const [usingRealData, setUsingRealData] = useState(false);

  // Fetch real data on mount
  useEffect(() => {
    api
      .fetchGeometricAnomaly()
      .then((response) => {
        const transformed = transformApiResponse(response);
        // Only use API data if it has patterns and words
        if (transformed.patterns.labelPairs.length > 0 && transformed.receipt.words.length > 0) {
          setData(transformed);
          setUsingRealData(true);
        }
      })
      .catch((error) => {
        console.warn("Failed to fetch geometric anomaly data, using mock data:", error);
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, []);

  // Animation sequence when component comes into view
  useEffect(() => {
    if (!inView || hasStartedAnimation.current) return;

    hasStartedAnimation.current = true;
    setStage("labeling");

    // Stage 1: Labeling words (0-0.5)
    const labelingInterval = setInterval(() => {
      setAnimationProgress((prev) => {
        if (prev >= 0.5) {
          clearInterval(labelingInterval);
          return prev;
        }
        return prev + 0.02;
      });
    }, 50);

    // Stage 2: Detecting anomalies
    const detectingTimeout = setTimeout(() => {
      setStage("detecting");
      const detectingInterval = setInterval(() => {
        setAnimationProgress((prev) => {
          if (prev >= 0.7) {
            clearInterval(detectingInterval);
            return prev;
          }
          return prev + 0.02;
        });
      }, 50);
    }, 1500);

    // Stage 3: Pattern analysis
    const patternTimeout = setTimeout(() => {
      setStage("pattern");
      // Highlight the flagged word
      setHighlightedWordId(data.flaggedWord?.wordId || null);

      const patternInterval = setInterval(() => {
        setAnimationProgress((prev) => {
          if (prev >= 1) {
            clearInterval(patternInterval);
            return 1;
          }
          return prev + 0.02;
        });
      }, 50);
    }, 2500);

    // Stage 4: Complete
    const completeTimeout = setTimeout(() => {
      setStage("complete");
    }, 4000);

    return () => {
      clearInterval(labelingInterval);
      clearTimeout(detectingTimeout);
      clearTimeout(patternTimeout);
      clearTimeout(completeTimeout);
    };
  }, [inView, data]);

  const handleWordClick = (wordId: string) => {
    // Find if this word is the flagged one
    const word = data.receipt.words.find((w) => w.id === wordId);
    if (word?.isFlagged) {
      // Find the pattern that matches this word's anomaly
      // For now, select the first pattern (SUBTOTAL -> GRAND_TOTAL)
      setSelectedPatternIndex(0);
      setHighlightedWordId(wordId);
    }
  };

  const currentPattern = data.patterns.labelPairs[selectedPatternIndex];

  // Only show flagged word info when the selected pattern matches
  const showFlaggedWord =
    currentPattern?.from === "SUBTOTAL" && currentPattern?.to === "GRAND_TOTAL";

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
      {/* Stage indicator */}
      <div className={styles.stageIndicator}>
        {(["labeling", "detecting", "pattern", "complete"] as AnimationStage[]).map(
          (s, index) => (
            <React.Fragment key={s}>
              {index > 0 && (
                <div
                  className={`${styles.stageConnector} ${
                    stage === s ||
                    (["detecting", "pattern", "complete"].indexOf(stage) > index - 1)
                      ? styles.stageConnectorActive
                      : ""
                  }`}
                />
              )}
              <div
                className={`${styles.stageStep} ${
                  stage === s ? styles.stageStepActive : ""
                } ${
                  ["detecting", "pattern", "complete"].indexOf(stage) >
                  ["labeling", "detecting", "pattern", "complete"].indexOf(s)
                    ? styles.stageStepComplete
                    : ""
                }`}
              >
                <span
                  className={`${styles.stageDot} ${
                    stage === s ? styles.stageDotActive : ""
                  } ${
                    ["detecting", "pattern", "complete"].indexOf(stage) >
                    ["labeling", "detecting", "pattern", "complete"].indexOf(s)
                      ? styles.stageDotComplete
                      : ""
                  }`}
                />
                {STAGE_LABELS[s]}
              </div>
            </React.Fragment>
          )
        )}
      </div>

      {/* Main panels */}
      <div className={styles.panelsContainer}>
        <ReceiptView
          words={data.receipt.words}
          animationProgress={Math.min(animationProgress * 2, 1)}
          highlightedWordId={highlightedWordId}
          onWordHover={setHighlightedWordId}
          onWordClick={handleWordClick}
        />

        <PatternScatterPlot
          pattern={currentPattern}
          flaggedWord={showFlaggedWord ? data.flaggedWord : null}
          animationProgress={animationProgress}
          allPatterns={data.patterns.labelPairs}
          selectedPatternIndex={selectedPatternIndex}
          onSelectPattern={setSelectedPatternIndex}
        />
      </div>
    </div>
  );
};

export default GeometricAnomalyVisualization;
