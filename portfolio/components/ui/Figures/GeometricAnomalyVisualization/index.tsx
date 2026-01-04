import React, { useState, useEffect, useRef } from "react";
import { useInView } from "react-intersection-observer";
import ReceiptView from "./ReceiptView";
import PatternScatterPlot from "./PatternScatterPlot";
import { GeometricAnomalyData } from "./mockData";
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
          currentLabel: response.flagged_word.current_label,
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
  const [data, setData] = useState<GeometricAnomalyData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch real data on mount
  useEffect(() => {
    api
      .fetchGeometricAnomaly()
      .then((response) => {
        const transformed = transformApiResponse(response);
        if (transformed.patterns.labelPairs.length > 0 && transformed.receipt.words.length > 0) {
          setData(transformed);
        } else {
          setError("No geometric anomaly data available");
        }
      })
      .catch((err) => {
        console.error("Failed to fetch geometric anomaly data:", err);
        setError("Failed to load visualization data");
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, []);

  // Animation sequence when component comes into view
  useEffect(() => {
    if (!inView || !data || hasStartedAnimation.current) return;

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

  // Show loading state
  if (isLoading) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loadingState}>Loading visualization...</div>
      </div>
    );
  }

  // Show error state
  if (error || !data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.errorState}>{error || "No data available"}</div>
      </div>
    );
  }

  // Find the pattern index that matches the flagged word's relationship
  const matchingPatternIndex = data.flaggedWord
    ? data.patterns.labelPairs.findIndex(
        (p) =>
          p.from === data.flaggedWord?.referenceLabel &&
          p.to === data.flaggedWord?.currentLabel
      )
    : -1;

  // Auto-select matching pattern on first render (use effect to set initial index)
  const initialPatternIndex = matchingPatternIndex >= 0 ? matchingPatternIndex : 0;

  const handleWordClick = (wordId: string) => {
    // Find if this word is the flagged one
    const word = data.receipt.words.find((w) => w.id === wordId);
    if (word?.isFlagged && matchingPatternIndex >= 0) {
      // Select the pattern that matches this word's anomaly
      setSelectedPatternIndex(matchingPatternIndex);
      setHighlightedWordId(wordId);
    }
  };

  // Use initialPatternIndex if selectedPatternIndex is still 0 and we have a match
  const effectivePatternIndex =
    selectedPatternIndex === 0 && matchingPatternIndex >= 0
      ? matchingPatternIndex
      : selectedPatternIndex;

  const currentPattern = data.patterns.labelPairs[effectivePatternIndex];

  // Show flagged word info when the selected pattern matches the anomaly
  const showFlaggedWord =
    data.flaggedWord &&
    currentPattern?.from === data.flaggedWord.referenceLabel &&
    currentPattern?.to === data.flaggedWord.currentLabel;

  return (
    <div ref={ref} className={styles.container}>
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
          selectedPatternIndex={effectivePatternIndex}
          onSelectPattern={setSelectedPatternIndex}
        />
      </div>
    </div>
  );
};

export default GeometricAnomalyVisualization;
